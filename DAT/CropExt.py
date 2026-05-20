"""
Extension classes enhance TouchDesigner components with python. An
extension is accessed via ext.ExtensionClassName from any operator
within the extended component. If the extension is promoted via its
Promote Extension parameter, all its attributes with capitalized names
can be accessed externally, e.g. op('yourComp').PromotedFunction().

Help: search "Extensions" in wiki
"""

from TDStoreTools import StorageManager
import TDFunctions as TDF

class crop:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        
        TDF.createProperty(self, 'CropLeft', value=0, dependable=True, readOnly=False)
        TDF.createProperty(self, 'CropRight', value=0, dependable=True, readOnly=False)
        TDF.createProperty(self, 'CropBottom', value=0, dependable=True, readOnly=False)
        TDF.createProperty(self, 'CropTop', value=0, dependable=True, readOnly=False)

    def Update(self):
        self.CropLeft   = op('crop_u')['tx'] - op('crop_w')['width']
        self.CropRight  = op('crop_u')['tx'] + op('crop_w')['width']
        self.CropBottom = op('crop_v')['ty'] - op('crop_h')['height']
        self.CropTop    = op('crop_v')['ty'] + op('crop_h')['height']