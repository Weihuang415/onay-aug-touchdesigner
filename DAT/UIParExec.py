"""
Parameter Execute DAT callbacks for the UI comp's custom parameters.

Hook-up (inside /project1/UI):
  1. Add a Parameter Execute DAT (e.g. parexec_ui).
  2. File -> DAT/UIParExec.py, turn on Sync to File, right-click -> Reload File.
  3. On the DAT: Active = On, OPs = .. , Parameters = * ,
     Custom = On, On Pulse = On.
"""


def _ui_url():
    port = 9980
    try:
        port = int(op('webserver1').par.port.eval())
    except Exception:
        pass
    return f'http://127.0.0.1:{port}'


def onPulse(par):
    if par.name == 'Openbrowserui':
        import webbrowser
        webbrowser.open(_ui_url())
    return


def onValueChange(par, prev):
    return


def onExpressionChange(par, val, prev):
    return


def onExportChange(par, val, prev):
    return


def onEnableChange(par, val, prev):
    return


def onModeChange(par, val, prev):
    return
