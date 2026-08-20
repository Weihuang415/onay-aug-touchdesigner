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

    # role -> (videodevin path, keyword that must appear in the device name).
    # Auto-assignment only activates once the capture cards are renamed in
    # the Magewell USB Capture Utility (e.g. "SDI Inside" / "SDI Outside") —
    # with factory names both cards are identical and it skips gracefully.
    CAMERA_ROLES = {
        '/project1/YOLO_FACES/videodevin1': 'inside',
        '/project1/CAM_BEHIND/videodevin1': 'outside',
    }

    def Startup(self) -> None:
        print("StartupExt.Startup()")
        self.AddDependenciesToPath()
        op.SETTINGS.Startup()
        self.OpenUI()
        self.RestoreInsideCam()
        # USB devices can enumerate late — check camera assignment after ~10 s
        run("op.STARTUP.AutoAssignCameras()", delayFrames=600)

    def AutoAssignCameras(self) -> None:
        """Point each Video Device In at the card whose NAME matches its
        role, so swapped USB ports / shuffled enumeration order can never
        cross the cameras. Requires uniquely named cards (Magewell rename);
        does nothing while both cards report the same factory name."""
        plan = {}
        for path, key in self.CAMERA_ROLES.items():
            v = op(path)
            if v is None:
                continue
            labels = [str(l).lower() for l in v.par.device.menuLabels]
            hits = [i for i, l in enumerate(labels) if key in l]
            if len(hits) != 1:
                print(f"[auto-assign] '{key}': {len(hits)} name match(es) — "
                      "rename the cards in Magewell USB Capture Utility "
                      "to enable auto-assignment; skipping")
                return
            plan[path] = hits[0]
        wrong = {p: i for p, i in plan.items()
                 if op(p).par.device.menuIndex != i}
        if not wrong:
            print("[auto-assign] camera assignment already correct")
            return
        print(f"[auto-assign] fixing {len(wrong)} camera(s):", wrong)
        # release every wrong device first, then reassign together — avoids
        # the two captures fighting over a card mid-swap
        for p in wrong:
            op(p).par.active = 0
        code = "\n".join(
            f"op('{p}').par.device.menuIndex = {i}\nop('{p}').par.active = 1"
            for p, i in wrong.items())
        run(code, delayMilliSeconds=800)

    def RestoreInsideCam(self) -> None:
        """Re-apply the index-based CAM Inside camera choice picked in the
        web UI (stored on op.UI). MediaPipe's page loads slowly, so wait
        ~20 s; harmless no-op when the stored choice is 'auto'."""
        run(
            "mod('/project1/UI/webserver1_callbacks').ApplyStoredInsideCam()",
            delayFrames=1200,
        )

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
