from TDStoreTools import StorageManager
import TDFunctions as TDF


class SettingsExt:
    def __init__(self, ownerComp):
        # The component to which this extension is attached
        self.ownerComp = ownerComp
        self.settingsTable = op("table_settings")
        self.NumQuestions: int = 4
        self.NumWrongAnswers: int = 3

        # properties
        TDF.createProperty(self, "AssetPath", value="", dependable=True, readOnly=False)
        TDF.createProperty(self, "RecordingPath", value="", dependable=True, readOnly=False)
        TDF.createProperty(self, "ResX", value="", dependable=True, readOnly=False)
        TDF.createProperty(self, "ResY", value="", dependable=True, readOnly=False)
        TDF.createProperty(self, "CamX", value="", dependable=True, readOnly=False)
        TDF.createProperty(self, "CamY", value="", dependable=True, readOnly=False)

    def Startup(self) -> None:
        print("SettingsExt.Startup()")
        node = var("NODE")
        if node == "":
            node = "WF"
        print(f"Settings: Running as node {node}")

        self.AssetPath = self.settingsTable[node, "asset_path"].val
        print(f"Settings: AssetPath {self.AssetPath}")

        # Screen Resolution
        self.ResX = self.settingsTable[node, "resX"].val
        print(f"Settings: ResX {self.ResX}")
        self.ResY = self.settingsTable[node, "resY"].val
        print(f"Settings: ResY {self.ResY}")

        # Camera Resolution
        self.CamX = self.settingsTable[node, "camX"].val
        print(f"Settings: CamX {self.CamX}")
        self.CamY = self.settingsTable[node, "camY"].val
        print(f"Settings: CamY {self.CamY}")

