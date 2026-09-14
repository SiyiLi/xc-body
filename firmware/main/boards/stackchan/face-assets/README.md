# XC Body Face Assets

The GIFs under `source/` are the tracked artwork and timing sources. Their
authoring backgrounds may differ. Do not copy them directly into the firmware
assets partition.

Generate the packaged GIFs with Pillow 11.3.0:

```sh
python3 firmware/scripts/prepare_stackchan_face_assets.py --write
```

Check that every packaged GIF exactly matches its source artwork and timing:

```sh
python3 firmware/scripts/prepare_stackchan_face_assets.py
```

The converter normalizes every source GIF's flat background to canonical
`#061019`. Packaged GIFs remain opaque and delta-encoded so the firmware can
redraw only changed regions. Every other pixel and the visible timeline are
preserved exactly.
