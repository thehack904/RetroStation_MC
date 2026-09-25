import os
from unittest.mock import patch

from app import vaapi_driver


def test_intel_candidates_try_i965_and_ihd():
    with patch.object(vaapi_driver, '_drm_vendor_ids', return_value={'0x8086'}):
        with patch.dict(os.environ, {}, clear=True):
            assert vaapi_driver._candidate_drivers()[:2] == ['i965', 'iHD']


def test_configure_exports_working_driver():
    vaapi_driver.configure_vaapi_driver.cache_clear()
    with patch.object(vaapi_driver, '_candidate_drivers', return_value=['i965', 'iHD']), \
         patch.object(vaapi_driver, '_probe_driver', side_effect=lambda d, ffmpeg_bin='ffmpeg': d == 'i965'), \
         patch.dict(os.environ, {}, clear=True):
        selected = vaapi_driver.configure_vaapi_driver()
        assert selected == 'i965'
        assert os.environ['LIBVA_DRIVER_NAME'] == 'i965'
        assert os.environ['RSMC_VAAPI_DRIVER'] == 'i965'
    vaapi_driver.configure_vaapi_driver.cache_clear()
