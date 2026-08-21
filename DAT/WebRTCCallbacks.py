"""
WebRTC DAT callbacks (synced to /project1/UI/webrtc1_callbacks).

All logic lives in UIExt.py (the web server callbacks module) so the
signaling state and the WebSocket clients are in one place — these hooks
just forward every event there.
"""


def _ui():
    return op('/project1/UI/webserver1_callbacks').module


def onOffer(webrtcDAT, connectionId, localSdp):
    _ui().WRTC_onOffer(webrtcDAT, connectionId, localSdp)


def onAnswer(webrtcDAT, connectionId, localSdp):
    _ui().WRTC_onAnswer(webrtcDAT, connectionId, localSdp)


def onIceCandidate(webrtcDAT, connectionId, candidate, lineIndex, sdpMid):
    _ui().WRTC_onIceCandidate(webrtcDAT, connectionId, candidate, lineIndex, sdpMid)


def onIceCandidateError(webrtcDAT, connectionId, errorText):
    debug('webrtc ice error [{}]: {}'.format(connectionId, errorText))


def onConnectionStateChange(webrtcDAT, connectionId, newState):
    _ui().WRTC_onConnectionStateChange(webrtcDAT, connectionId, newState)


def onIceConnectionStateChange(webrtcDAT, connectionId, newState):
    debug('webrtc ice state [{}]: {}'.format(connectionId, newState))
