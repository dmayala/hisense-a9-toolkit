#!/usr/bin/env python3
"""Host regression for source, staged or vendor.img-extracted codec XML.

Android 16 Codec2InfoBuilder applies XML rank after component traits;
MediaCodecList sorts ascending. C2Store gives software video rank 512.
Sources: frameworks/av media/codec2/{sfplugin/Codec2InfoBuilder.cpp,
vndk/C2Store.cpp} and media/libstagefright/MediaCodecList.cpp, android16-release.
Pass --software-xml from the GSI swcodec APEX to check the installed entries.
"""
import argparse
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--xml', type=Path, default=HERE.parent/'vendor/etc/media_codecs_c2.xml')
parser.add_argument('--software-xml', type=Path)
args = parser.parse_args()
vendor = ET.parse(args.xml).getroot()
# Minimal Android 16 declarations for the dependency-free regression. The
# build check supplies the actual GSI XML instead of this fixture.
software = (ET.parse(args.software_xml).getroot() if args.software_xml else ET.fromstring(
    '<MediaCodecs><Encoders>'
    '<MediaCodec name="c2.android.avc.encoder" type="video/avc" />'
    '<MediaCodec name="c2.android.hevc.encoder" type="video/hevc" />'
    '</Encoders></MediaCodecs>'))


class EncoderRank(unittest.TestCase):
    def test_software_first_for_both_mime_types(self):
        for codec, mime in (('avc', 'video/avc'), ('hevc', 'video/hevc')):
            with self.subTest(mime=mime):
                hw = vendor.find(f'Encoders/MediaCodec[@name="c2.v4l2.{codec}.encoder"]')
                sw = software.find(f'Encoders/MediaCodec[@name="c2.android.{codec}.encoder"]')
                self.assertIsNotNone(hw)
                self.assertIsNotNone(sw)
                self.assertEqual(hw.get('type'), mime)
                self.assertEqual(sw.get('type'), mime)
                self.assertNotEqual(sw.get('enabled'), 'false')
                sw_rank = int(sw.get('rank', '512'))
                hw_rank = int(hw.attrib['rank'])
                self.assertLess(sw_rank, hw_rank, 'unqualified hardware encoder selected by MIME')
                ordered = sorted((hw, sw), key=lambda c: int(c.get('rank', '512')))
                self.assertEqual(ordered[0].get('name'), sw.get('name'))
                # Ranking must retain exact-name hardware selection for row 18.
                self.assertNotEqual(hw.get('enabled'), 'false')
                self.assertEqual(len(vendor.findall(
                    f'Encoders/MediaCodec[@name="c2.v4l2.{codec}.encoder"]')), 1)

    def test_hardware_decoders_keep_priority(self):
        for codec in ('avc', 'hevc'):
            decoder = vendor.find(f'Decoders/MediaCodec[@name="c2.v4l2.{codec}.decoder"]')
            self.assertIsNotNone(decoder)
            self.assertEqual(decoder.get('rank'), '256')


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)
