import umdapy
from pathlib import Path as pt

hiddenimports = ["umdapy.server", "umdapy.getVersion"]
icons_path = pt(umdapy.__file__).parent / "../icons/*"
icons_path = icons_path.resolve().__str__()
datas = [(icons_path, "icons")]
