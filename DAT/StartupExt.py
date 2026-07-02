from TDStoreTools import StorageManager
import TDFunctions as TDF
import os
import sys


class StartupExt:
    """
    StartupExt description
    """

    def __init__(self, ownerComp) -> None:
        # The component to which this extension is attached
        self.ownerComp = ownerComp

    def Startup(self) -> None:
        print("StartupExt.Startup()")
        self.AddDependenciesToPath()
        op.SETTINGS.Startup()
        self.OpenUI()

    def OpenUI(self) -> None:
        """Open the web control UI in the default browser once the project is up"""
        port = 9980
        try:
            port = int(op.UI.op("webserver1").par.port.eval())
        except Exception:
            pass
        run(
            f"import webbrowser; webbrowser.open('http://127.0.0.1:{port}')",
            delayFrames=120,
        )


    def AddDependenciesToPath(self) -> None:
        """Add site-packages from the .venv to the path"""
        dep_path = f"{project.folder}/DEP/.venv/Lib/site-packages/"
        norm_dep_path = os.path.normpath(dep_path)
        if norm_dep_path not in sys.path:
            sys.path.insert(0, norm_dep_path)
