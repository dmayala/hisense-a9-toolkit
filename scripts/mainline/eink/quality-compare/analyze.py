#!/usr/bin/env python3
"""Offline camera comparison. Reads saved files only; never imports capture.py."""
import argparse
from functools import lru_cache
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import sys
import cv2
import numpy as np

POINTS=np.float32([[64,64],[760,64],[760,1584],[64,1584]])
ROI=np.s_[180:1468,140:684]
MODES={513:'Balanced',515:'Clear',518:'Smooth',521:'Speed'}


def json_default(value):
    if isinstance(value,np.generic): return value.item()
    if isinstance(value,np.ndarray): return value.tolist()
    raise TypeError(f'{type(value).__name__} is not JSON serializable')


def gray(path):
    im=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
    if im is None: raise ValueError('cannot decode '+str(path))
    return im.astype(np.float32)/255


def homography(points):
    points=np.float32(points)
    if points.shape!=(4,2) or not np.isfinite(points).all(): raise ValueError('four fiducial centers required')
    if not cv2.isContourConvex(points.astype(np.int32)): raise ValueError('fiducial centers must be TL TR BR BL')
    return cv2.getPerspectiveTransform(points,POINTS)


def warp(im,H): return cv2.warpPerspective(im,H,(824,1648),flags=cv2.INTER_LINEAR)


def marker_score(im,H):
    w=warp(im,H); scores=[]
    for i,(x,y) in enumerate(POINTS.astype(int)):
        patch=w[y-40:y+40,x-40:x+40]
        low,high=np.percentile(patch,[10,90])
        if high-low<.06: return 0.
        # Use interior rings; exclude interpolation/optical edge pixels.
        yy,xx=np.mgrid[-40:40,-40:40]; radius=np.maximum(abs(xx+.5),abs(yy+.5))
        sizes=[40,30,20,12,5] if i==0 else [40,26,12]
        expected=np.ones((80,80)); edge=np.ones((80,80),bool)
        for n,size in enumerate(sizes):
            expected[radius<size]=n%2
            edge &= abs(radius-size)>2
        # Outer border belongs to the black first ring.
        scores.append(float(np.mean(((patch-low)/(high-low)>.5)[edge]==(expected>.5)[edge])))
    return min(scores)


def register(im):
    """Find nested squares, score all four templates to reject unrelated rectangles."""
    u8=np.uint8(np.clip(im*255,0,255)); candidates=[]
    otsu,_=cv2.threshold(u8,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    for threshold in sorted(set([int(otsu),45,75,105,135,165,195])):
        binary=np.uint8(u8<threshold)*255
        contours,hierarchy=cv2.findContours(binary,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None: continue
        hierarchy=hierarchy[0]
        for i,c in enumerate(contours):
            area=cv2.contourArea(c)
            if not 80<area<im.size*.025: continue
            depth=0; child=hierarchy[i,2]
            while child>=0: depth+=1; child=hierarchy[child,2]
            if depth<2: continue
            poly=cv2.approxPolyDP(c,cv2.arcLength(c,True)*.055,True)
            if len(poly)!=4 or not cv2.isContourConvex(poly): continue
            m=cv2.moments(c); center=np.array([m['m10']/m['m00'],m['m01']/m['m00']])
            # Keep the outer ring, rather than duplicate inner ring centers.
            match=next((v for v in candidates if np.linalg.norm(v['p']-center)<8),None)
            if match:
                if area>match['area']: match.update(p=center,area=area,depth=depth)
            else: candidates.append(dict(p=center,area=area,depth=depth))
    candidates=sorted(candidates,key=lambda c:c['area'],reverse=True)[:14]
    best=None
    for tl in [c for c in candidates if c['depth']>=4]:
        others=[c for c in candidates if c is not tl and .35< c['area']/tl['area'] <2.8]
        for group in itertools.combinations(others,3):
            pts=np.float32([tl['p'],*[c['p'] for c in group]])
            center=pts.mean(axis=0); order=np.argsort(np.arctan2(pts[:,1]-center[1],pts[:,0]-center[0]))
            order=np.roll(order,-int(np.where(order==0)[0][0])); pts=pts[order]
            if cv2.contourArea(pts)<10000 or not cv2.isContourConvex(pts.astype(np.int32)): continue
            H=homography(pts); score=marker_score(im,H)
            if best is None or score>best[0]: best=(score,pts,H)
            if score>.96: return pts,H,score
    if best is None or best[0]<.82:
        raise ValueError('four fiducials not resolved; supply --stock-centers / --mainline-centers TL,TR,BR,BL in photo pixels')
    return best[1],best[2],best[0]


def anchors(im):
    # Same unchanging physical white margin + black outer fiducial rings in every image.
    white=float(np.median(im[24:94,140:684]))
    black=np.concatenate([im[y-35:y+35,x-35:x-31].ravel() for x,y in POINTS.astype(int)])
    black=float(np.median(black))
    if white-black<.06: raise ValueError('camera contrast too low at fiducials')
    return black,white


def normalized(im):
    b,w=anchors(im)
    return (im-b)/(w-b)


def levels(ramps,black,white,floor=.02):
    span=white-black
    if np.percentile(span[ROI],10)<.2: raise ValueError('black/white calibration has insufficient optical contrast')
    values=[]; noises=[]
    for n in range(16):
        x=120+(n%4)*146; y=160+(n//4)*332
        box=np.s_[y+60:y+272,x+32:x+114]
        patches=np.array([((r-black)/np.maximum(span,.1))[box] for r in ramps])
        # Temporal medians plus local block variation, not pixel-count SEM.
        values.append(float(np.median(patches)))
        med=np.median(patches,axis=0)
        tiles=[np.median(v) for row in np.array_split(med,4,axis=0) for v in np.array_split(row,4,axis=1)]
        temporal=1.4826*np.median(np.abs(np.median(patches,axis=(1,2))-np.median(patches)))
        spatial=1.4826*np.median(np.abs(np.array(tiles)-np.median(tiles)))
        noises.append(float(max(temporal,spatial)))
    thresholds=[max(floor,3*np.hypot(noises[n],noises[n+1])) for n in range(15)]
    adjacent=sum(values[n+1]-values[n]>thresholds[n] for n in range(15))
    # Longest increasing separated subset of the 16 ordered stimuli.
    best=[1]*16
    for j in range(16):
        for i in range(j):
            if values[j]-values[i]>max(floor,3*np.hypot(noises[i],noises[j])):
                best[j]=max(best[j],best[i]+1)
    return dict(count=max(best),adjacent_resolved=adjacent,means=values,noise=noises,
                separation_floor=floor,monotonic_violations=sum(np.diff(values)<-floor))


def ghost(post,clean,span,pattern):
    residual=(clean-post)/np.maximum(span,.1)
    mask=np.zeros_like(pattern,dtype=np.uint8); mask[ROI]=1
    # Threshold inside dark glyphs; eroding thin strokes would remove the text sample.
    dark=pattern<.3
    light=cv2.erode(np.uint8(pattern>.94),np.ones((3,3),np.uint8))>0
    d=residual[dark & (mask>0)]; l=residual[light & (mask>0)]
    if len(d)<100 or len(l)<100: raise ValueError('ghost mask has too few pixels')
    contrast=float(np.median(d)-np.median(l))
    return dict(contrast=abs(contrast),signed_contrast=contrast,
                rms=float(np.sqrt(np.mean(np.clip(residual[ROI],-.5,.5)**2))))


def latency(frames,event,read,final):
    """Three successive near-final photos, with no later relapse in the step."""
    before=[f for f in frames if event['start']-.8<=f['end']<event['start']]
    during=[f for f in frames if f['start']>=event['start'] and f['end']<=event['end']]
    if not before or len(during)<3: return dict(status='MISSING_FRAMES')
    # Downsample to suppress camera noise and avoid weighting JPEG edges.
    def tiny(im): return cv2.resize(normalized(im)[ROI],(68,161),interpolation=cv2.INTER_AREA)
    target=tiny(final); initial=tiny(read(before[-1]['file']))
    amplitude=float(np.mean(abs(initial-target)))
    if amplitude<.04: return dict(status='NO_RESOLVABLE_CHANGE',amplitude=amplitude)
    observed=[tiny(read(f['file'])) for f in during]
    distances=[float(np.mean(abs(im-target))) for im in observed]
    changes=[float(np.mean(abs(im-initial))) for im in observed]
    tolerance=.1*amplitude
    # Last non-settled image must precede >=3 settled images. Transient white flashes cannot pass.
    unsettled=[i for i,d in enumerate(distances) if d>tolerance]
    first=(unsettled[-1]+1) if unsettled else 0
    if len(during)-first<3: return dict(status='NOT_SETTLED',amplitude=amplitude)
    previous=during[first-1] if first else before[-1]
    sample=during[first]
    onset=next((i for i,d in enumerate(changes) if d>.1*amplitude),None)
    if onset is None: return dict(status='NO_RESOLVABLE_ONSET',amplitude=amplitude)
    onset_previous=during[onset-1] if onset else before[-1]
    onset_sample=during[onset]
    gaps=[b['start']-a['end'] for a,b in zip(during,during[1:])]
    return dict(status='OBSERVED',lower_ms=max(0,(previous['start']-event['start'])*1000),
                upper_ms=(sample['end']-event['start'])*1000,
                onset_lower_ms=max(0,(onset_previous['start']-event['start'])*1000),
                onset_upper_ms=(onset_sample['end']-event['start'])*1000,
                transition_lo_ms=max(0,(previous['start']-onset_sample['end'])*1000),
                transition_hi_ms=max(0,(sample['end']-onset_previous['start'])*1000),
                max_sample_gap_ms=max(gaps,default=0)*1000,amplitude=amplitude,
                camera_age='UNKNOWN; HTTP brackets are not exposure timestamps')


def parse_centers(text):
    if text is None: return None
    values=[float(v) for v in text.replace(';',',').split(',')]
    return np.array(values,dtype=np.float32).reshape(4,2)


def analyze_run(path,patterns,out,centers=None,modes=None):
    meta=json.loads((path/'run.json').read_text()); events=json.loads((path/'events.json').read_text())
    for file in patterns.glob('*.png'):
        if meta['patterns'].get('patterns/'+file.name)!=hashlib.sha256(file.read_bytes()).hexdigest():
            raise ValueError('analysis pattern differs from captured APK: '+file.name)
    frames=[json.loads(line) for line in (path/'frames.jsonl').read_text().splitlines()]
    if not frames: raise ValueError('no saved frames')
    if any(a['end']>b['start'] or a['start']>a['end'] for a,b in zip(frames,frames[1:])):
        raise ValueError('non-monotonic/overlapping HTTP brackets')
    # Pick an orientation-calibrated clean white, not a screenshot or the current live camera.
    ref=next(e for e in events if e['step']=='white_clean' and e.get('complete') and (modes is None or e['mode'] in modes))
    sample=next(f for f in frames if ref['settled']<=f['start'] and f['end']<=ref['end'])
    reference=gray(path/sample['file'])
    if centers is None: pts,H,score=register(reference)
    else:
        pts=centers; H=homography(pts); score=marker_score(reference,H)
        if score<.82: raise ValueError('supplied centers fail fiducial template check')
    out.mkdir(parents=True,exist_ok=True)
    preview=cv2.imread(str(path/sample['file'])); cv2.polylines(preview,[np.int32(pts)],True,(0,255,0),3)
    cv2.imwrite(str(out/'registration.jpg'),preview)
    (out/'registration.json').write_text(json.dumps(dict(centers=pts.tolist(),score=score,source=sample['file']),indent=2)+'\n')
    @lru_cache(maxsize=16)
    def read(file): return warp(gray(path/file),H)
    def finals(event):
        rows=[f for f in frames if event['settled']<=f['start'] and f['end']<=event['end']]
        if len(rows)<3: raise ValueError('fewer than three settled photos: '+event['step'])
        return [read(f['file']) for f in rows]
    groups={}; results=[]; issues=[]
    if meta['status']!='CAPTURED': issues.append('capture incomplete')
    cleanup=json.loads((path/'cleanup.json').read_text())
    if cleanup['errors'] or not cleanup['camera_stopped']: issues.append('cleanup incomplete')
    for e in events:
        if e.get('complete') and (modes is None or e['mode'] in modes):
            groups.setdefault((e['mode'],e['rep']),{})[e['step']]=e
    for (mode,rep),steps in groups.items():
        result=dict(mode=mode,rep=rep,issues=[],sequence_complete=False)
        try:
            need=['black','white_clean','ramp','checker','white_checker','text','scroll','white_text','white_clear']
            if set(steps)!=set(need): raise ValueError('incomplete pattern sequence')
            result['sequence_complete']=True
            photos={name:finals(steps[name]) for name in need}
            # Border anchors normalize a common affine camera response; large exposure changes disqualify.
            anchor=np.array([anchors(im) for ims in photos.values() for im in ims])
            drift=float(np.max(np.ptp(anchor,axis=0))/(np.median(anchor[:,1]-anchor[:,0])))
            result['anchor_drift']=drift
            if drift>.05: result['issues'].append('lighting/exposure drift >5% of optical range')
            if any(marker_score(ims[-1],np.eye(3))<.80 for ims in photos.values()):
                result['issues'].append('fiducials moved or unresolved')
            averaged={name:np.median(np.array([normalized(im) for im in ims]),axis=0) for name,ims in photos.items()}
            black,white=averaged['black'],averaged['white_clean']; span=white-black
            result['greys']=levels([normalized(im) for im in photos['ramp']],black,white)
            # Verify that contrast screens really reached the glass; flat/stuck panels cannot score low ghost.
            checker=gray(patterns/'checker.png'); scroll=gray(patterns/'scroll.png')
            for name,pattern in [('checker',checker),('scroll',scroll)]:
                raw=float(np.corrcoef(averaged[name][ROI].ravel(),pattern[ROI].ravel())[0,1])
                result[name+'_raw_correlation']=raw if np.isfinite(raw) else None
                # Compare at camera-resolvable bandwidth; native thin glyphs
                # falsely failed stock's saved photos (.51 -> .83 after blur).
                observed=cv2.GaussianBlur(averaged[name],(0,0),2)
                expected=cv2.GaussianBlur(pattern,(0,0),2)
                correlation=float(np.corrcoef(observed[ROI].ravel(),expected[ROI].ravel())[0,1])
                result[name+'_correlation']=correlation if np.isfinite(correlation) else 0
                if not np.isfinite(correlation) or correlation<.65: result['issues'].append(name+' pattern mismatch')
            result['pattern_check_blur_sigma_native_px']=2
            result['ghost_checker']=ghost(averaged['white_checker'],averaged['white_clear'],span,checker)
            result['ghost_text']=ghost(averaged['white_text'],averaged['white_clear'],span,scroll)
            # A visibly uncleared reference cannot establish zero ghosting.
            clean_delta=float(np.sqrt(np.mean((averaged['white_clear'][ROI]-white[ROI])**2)))
            result['clean_white_difference']=clean_delta
            if clean_delta>.03: result['issues'].append('cleared white references differ >3%')
            result['latencies']={name:latency(frames,steps[name],read,np.median(np.array(photos[name]),axis=0))
                                 for name in ['ramp','checker','white_checker','text','scroll','white_text']}
            for name in ['ramp','white_checker','white_text']:
                cv2.imwrite(str(out/f'{mode}-r{rep}-{name}.png'),np.uint8(np.clip(averaged[name],0,1)*255))
            result['qualified']=not result['issues']
        except (ValueError,KeyError,StopIteration) as error:
            result['qualified']=False; result['issues'].append(str(error))
        results.append(result)
    if len(groups)!=(len(MODES) if modes is None else len(modes))*meta['repeats']:
        issues.append('missing modes/repetitions')
    return dict(meta=meta,issues=issues,results=results)


def summary(run,mode,allow_partial=False,reps=None):
    rows=[r for r in run['results'] if r['mode']==mode and (reps is None or r['rep'] in reps)]
    valid=[r for r in rows if r.get('qualified')]
    fatal=[s for s in run['issues'] if s not in ('capture incomplete','missing modes/repetitions')]
    expected=len(reps) if allow_partial and reps is not None else run['meta']['repeats']
    complete=bool(expected) and len(valid)==expected and not (fatal if allow_partial else run['issues'])
    measured=[r for r in rows if all(k in r for k in ['greys','ghost_checker','ghost_text','latencies'])]
    if not measured: return dict(qualified=False,n=0)
    m=lambda field:statistics.median(field(r) for r in measured)
    timing=[t for r in measured for name,t in r['latencies'].items() if name!='scroll' and t['status']=='OBSERVED']
    return dict(qualified=complete,n=len(measured),greys=m(lambda r:r['greys']['count']),
                ghost=m(lambda r:max(r['ghost_checker']['contrast'],r['ghost_text']['contrast'])),
                ghost_checker=m(lambda r:r['ghost_checker']['contrast']),ghost_text=m(lambda r:r['ghost_text']['contrast']),
                latency_lo=statistics.median(t['lower_ms'] for t in timing) if timing else None,
                latency_hi=statistics.median(t['upper_ms'] for t in timing) if timing else None,
                transition_lo=statistics.median(t['transition_lo_ms'] for t in timing) if timing else None,
                transition_hi=statistics.median(t['transition_hi_ms'] for t in timing) if timing else None,
                timing_n=len(timing),timing_complete=len(timing)==5*len(measured))


def table(stock,mainline,allow_partial=False,modes=None):
    # Explicit comparison margins, not optical acceptance or a replacement for user judgement.
    lines=['| Mode | Stack | Median greys /16 ↑ | Ghost checker/text % ↓ | Host→camera settle ms ↓ | Optical transition ms ↓ | n | Verdict |',
           '|---|---|---:|---:|---:|---:|---:|---|']
    for mode,label in MODES.items():
        if modes is not None and mode not in modes: continue
        reps=None
        if allow_partial:
            complete=lambda run:{r['rep'] for r in run['results'] if r['mode']==mode and r.get('sequence_complete')}
            reps=complete(stock)&complete(mainline)
        s=summary(stock,mode,allow_partial,reps); m=summary(mainline,mode,allow_partial,reps)
        fields=['apk_sha256','settle','hz']+([] if allow_partial else ['repeats'])
        compatible=all(stock['meta'].get(k)==mainline['meta'].get(k) for k in fields)
        if not compatible or not s['qualified'] or not m['qualified']: verdict='INCONCLUSIVE'
        else:
            differences=[]
            if m['greys']<s['greys']-1: differences.append('fewer greys')
            if m['ghost']>s['ghost']+.01: differences.append('more ghost')
            if s['timing_complete'] and m['timing_complete'] and m['latency_lo']>s['latency_hi']+100:
                differences.append('slower observed settle')
            if differences: verdict='REGRESSION: '+', '.join(differences)
            elif not s['timing_complete'] or not m['timing_complete']: verdict='QUALITY MATCH; TIMING INCONCLUSIVE'
            elif m['latency_hi']>s['latency_lo']+100: verdict='QUALITY MATCH; TIMING OVERLAP'
            else: verdict='MATCH within declared margins'
        for stack,v in [('stock',s),('mainline',m)]:
            if not v['n']: cells=['—','—','—','—','0']
            else:
                timing='—' if v['latency_lo'] is None else f"{v['latency_lo']:.0f}–{v['latency_hi']:.0f}"
                transition='—' if v['transition_lo'] is None else f"{v['transition_lo']:.0f}–{v['transition_hi']:.0f}"
                cells=[f"{v['greys']:g}",f"{100*v['ghost_checker']:.2f}/{100*v['ghost_text']:.2f}",timing,transition,str(v['n'])]
            lines.append('| '+' | '.join([f'{label} ({mode})',stack,*cells,'reference' if stack=='stock' else verdict])+' |')
    if allow_partial:
        lines.insert(0,'PARTIAL optical comparison: matched completed repetitions only; aborted run is not a hardware pass.\n')
    return '\n'.join(lines)+'\n'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock',type=Path,required=True); p.add_argument('--mainline',type=Path,required=True)
    p.add_argument('--patterns',type=Path,default=Path('/home/user/A9-build/mainline/eink-quality-20261023/patterns'))
    p.add_argument('--out',type=Path,required=True); p.add_argument('--stock-centers'); p.add_argument('--mainline-centers')
    p.add_argument('--allow-partial',action='store_true',help='compare only matched completed repetitions; retain aborted-run status')
    p.add_argument('--modes',type=int,nargs='+',choices=list(MODES),help='mode words to analyze, e.g. 513 for Balanced')
    a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    try:
        stock=analyze_run(a.stock,a.patterns,a.out/'stock',parse_centers(a.stock_centers),a.modes)
        mainline=analyze_run(a.mainline,a.patterns,a.out/'mainline',parse_centers(a.mainline_centers),a.modes)
        if stock['meta']['backend']!='stock' or mainline['meta']['backend']!='mainline': raise ValueError('backend labels swapped')
        result=table(stock,mainline,a.allow_partial,a.modes)
        for label,run in [('stock',stock),('mainline',mainline)]:
            diagnostics=run['issues']+[f"{r['mode']} rep {r['rep']+1}: {', '.join(r['issues'])}" for r in run['results'] if r['issues']]
            if diagnostics: result+='\n'+label+': '+'; '.join(diagnostics)+'\n'
        result+='\nMargins: at most 1 fewer grey, +1 percentage point ghost contrast, +100 ms observed settle.\n'
        result+='Timing includes adb, app draw and unknown camera exposure/cache delay; intervals use HTTP brackets.\n'
        result+='Optical transition runs from first visible change to final settling; a fixed camera delay cancels, variable cache age does not.\n'
        result+='Snapshot timing is not panel-only refresh latency. Optical user judgement remains pending.\n'
        (a.out/'comparison.md').write_text(result)
        (a.out/'measurements.json').write_text(json.dumps(dict(stock=stock,mainline=mainline,
            allow_partial=a.allow_partial,modes=a.modes),indent=2,allow_nan=False,default=json_default)+'\n')
        print(result,end='')
    except (ValueError,KeyError,StopIteration,FileNotFoundError) as error:
        print('INCONCLUSIVE: '+str(error),file=sys.stderr); return 2
    return 0


if __name__=='__main__': sys.exit(main())
