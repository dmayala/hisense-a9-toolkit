"""Strict numeric probe results and synthetic MP4 validation; no transport."""
import hashlib
import json
from pathlib import Path
import re
import subprocess as sp

FFMPEG=Path('/nix/store/j1b3q6akcmxjamapv9qac6ibpd4g1d8b-ffmpeg-headless-7.1.1-bin/bin/ffmpeg')
FIELDS=('status avc_frames avc_eos avc_named hevc_frames hevc_eos hevc_named encoder_named '
        'surface_frames encoded_frames encoder_eos muxer_ok mp4_frames duration_us pts_span_us '
        'roundtrip_frames roundtrip_eos roundtrip_named bytes').split()
POINT=re.compile(r'HVENC_QUAL op=([0-9]{1,2}) phase=([0-2]) rc=(-?[0-9]{1,10}) frames=([0-9]{1,3})')
RESULT=re.compile('HVENC_RESULT '+' '.join(k+r'=(-?[0-9]{1,12})' for k in FIELDS))


def parse(text,rc):
    result=dict(command_rc=rc,completed=0)
    records=[]
    for line in text.splitlines():
        p=POINT.fullmatch(line)
        if p:result.update(zip(('last_op','last_phase','last_rc','last_frames'),map(int,p.groups())))
        r=RESULT.fullmatch(line)
        if r:records.append(dict(zip(FIELDS,map(int,r.groups()))))
    if len(records)==1:result.update(records[0],completed=1)
    return result


def passed(d):
    equal=dict(command_rc=0,completed=1,status=0,avc_frames=20,avc_eos=1,avc_named=1,
        hevc_frames=20,hevc_eos=1,hevc_named=1,encoder_named=1,surface_frames=30,
        encoded_frames=30,encoder_eos=1,muxer_ok=1,mp4_frames=30,
        roundtrip_frames=30,roundtrip_eos=1,roundtrip_named=1)
    return all(type(d.get(k)) is int and d[k]==v for k,v in equal.items()) and (
        2800000<=d.get('duration_us',0)<=4000000 and 2800000<=d.get('pts_span_us',0)<=3100000
        and 0<d.get('bytes',0)<=4*1024**2)


def inspect(path,frames=30,codec='h264'):
    """Demux+decode the entire generated file on host; never real user media."""
    result=dict(host_mp4_ok=0)
    try:
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=4*1024**2:return result
        p=sp.run([FFMPEG.with_name('ffprobe'),'-v','error','-count_frames','-show_streams','-show_format',
                  '-of','json',path],capture_output=True,text=True,timeout=10)
        if p.returncode:return result
        data=json.loads(p.stdout);streams=data['streams']
        if len(streams)!=1:return result
        video=streams[0]
        duration=float(video.get('duration',0));count=int(video.get('nb_read_frames',0))
        result.update(host_frames=count,host_duration_ms=int(duration*1000),
                      host_width=int(video.get('width',0)),host_height=int(video.get('height',0)))
        if (video.get('codec_name')!=codec or count!=frames or abs(duration-frames/10)>0.25
                or (result['host_width'],result['host_height'])!=(320,240)):return result
        p=sp.run([FFMPEG,'-nostdin','-v','error','-xerror','-i',path,'-f','null','-'],
                 stdout=sp.DEVNULL,stderr=sp.DEVNULL,timeout=10)
        result['host_mp4_ok']=int(p.returncode==0)
    except (OSError,ValueError,OverflowError,KeyError,TypeError,sp.TimeoutExpired):pass
    return result


def verify_bundle(bundle,mainline,encoder_receipt):
    manifest=json.loads((bundle/'manifest.json').read_text())
    assert manifest['schema']==1 and manifest['synthetic_only'] and not manifest['hardware_tested']
    assert hashlib.sha256(encoder_receipt.read_bytes()).hexdigest()==manifest['encoder_receipt_sha256']
    assert set(manifest['files'])=={'qualify','avc.mp4','hevc.mp4'}
    for name,digest in manifest['files'].items():
        p=bundle/name;assert not p.is_symlink() and p.is_file() and p.stat().st_size<=4*1024**2
        assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
    for name,digest in manifest['sources'].items():
        p=(mainline/name).resolve();assert p.is_relative_to(mainline.resolve())
        assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
    return manifest
