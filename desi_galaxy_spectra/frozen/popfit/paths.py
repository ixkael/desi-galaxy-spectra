"""Stub of the original ``popfit.paths`` module.

The frozen DESI extractor (``extract_desi_dr1_final.py``) does
``from popfit.paths import DESI_DR1_HEALPIX, FINAL_DESI_EXTRACTION``. Both names
are only the default values of two argparse options in the extractor's own
``main``. They are unused here: this package calls ``collect_rows`` and
``write_hdf5`` directly and always passes explicit paths, so both names are
``None``.
"""

DESI_DR1_HEALPIX = None
FINAL_DESI_EXTRACTION = None
