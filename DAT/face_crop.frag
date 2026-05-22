uniform float tx;
uniform float ty;
uniform float faceW;
uniform float faceH;
uniform vec2 uSrcRes;
uniform float uPad;

out vec4 fragColor;

void main()
{
    if (faceW <= 0.0 || faceH <= 0.0) {
        fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], vUV.st));
        return;
    }

    float resX = max(uSrcRes.x, 1.0);
    float resY = max(uSrcRes.y, 1.0);

    float realTY = -ty;

    float padX = faceW * uPad;
    float padY = faceH * uPad;

    float left  = (tx - padX) / resX;
    float top   = (realTY - padY) / resY;
    float cropW = (faceW + padX * 2.0) / resX;
    float cropH = (faceH + padY * 2.0) / resY;

    // 負數 padding 太大時保護
    if (cropW <= 0.0 || cropH <= 0.0) {
        fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], vUV.st));
        return;
    }

    float u = left  + vUV.s * cropW;
    float v = (1.0 - top - cropH) + vUV.t * cropH;

    vec2 uv = clamp(vec2(u, v), vec2(0.0), vec2(1.0));
    fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], uv));
}