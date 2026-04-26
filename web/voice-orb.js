// Adjutant voice orb — direct port of ElevenLabs Conversational AI orb.
// Source: github.com/elevenlabs/packages/convai-widget-core/src/orb
// (Orb.ts + OrbShader.vert + OrbShader.frag)
//
// WebGL2 single-fullscreen-quad fragment shader. Seven animated ovals
// in polar coordinates + two noisy concentric rings, all blended through
// a 4-color ramp (black → uColor1 → uColor2 → white).
//
// Modifications for Adjutant:
//   1. Perlin texture generated procedurally on init (stays offline —
//      ElevenLabs fetches a PNG from their CDN; we don't).
//   2. State-driven color pairs (Adjutant amber palette).
//   3. updateVolume(in, out) uses ElevenLabs' exact speed formula but
//      drives a single `uSpeed` uniform that advances uTime per-frame.
//   4. sRGB→linear gamma correction preserved (critical — without it
//      colors look washed out).

const VERT = `#version 300 es
precision highp float;
in vec2 position;
out vec2 vUv;
void main() {
  vUv = position * 0.5 + 0.5;
  gl_Position = vec4(position, 0, 1);
}`;

const FRAG = `#version 300 es
precision highp float;

uniform float uTime;
uniform float uOffsets[7];
uniform vec3 uColor1;
uniform vec3 uColor2;
uniform sampler2D uPerlinTexture;
uniform float uInputVolume;
uniform float uOutputVolume;

in vec2 vUv;
out vec4 outColor;

const float PI = 3.14159265358979323846;

bool drawOval(vec2 polarUv, vec2 polarCenter, float a, float b, bool reverseGradient, float softness, out vec4 color) {
  vec2 p = polarUv - polarCenter;
  float oval = (p.x * p.x) / (a * a) + (p.y * p.y) / (b * b);
  float edge = smoothstep(1.0, 1.0 - softness, oval);
  if (edge > 0.0) {
    float gradient = reverseGradient ? (1.0 - (p.x / a + 1.0) / 2.0) : ((p.x / a + 1.0) / 2.0);
    color = vec4(vec3(gradient), 0.8 * edge);
    return true;
  }
  return false;
}

vec3 colorRamp(float g, vec3 c1, vec3 c2, vec3 c3, vec3 c4) {
  if (g < 0.33) return mix(c1, c2, g * 3.0);
  if (g < 0.66) return mix(c2, c3, (g - 0.33) * 3.0);
  return mix(c3, c4, (g - 0.66) * 3.0);
}

vec2 hash2(vec2 p) {
  return fract(sin(vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)))) * 43758.5453);
}

float noise2D(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float n = mix(
    mix(dot(hash2(i + vec2(0.0, 0.0)), f - vec2(0.0, 0.0)),
        dot(hash2(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0)), u.x),
    mix(dot(hash2(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0)),
        dot(hash2(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0)), u.x),
    u.y
  );
  return 0.5 + 0.5 * n;
}

float sharpRing(vec2 uv, float theta, float time) {
  vec2 noiseCoord = vec2(theta / (2.0 * PI), time * 0.1) * 5.0;
  float noise = (noise2D(noiseCoord) - 0.5) * 4.0;
  return 1.0 + noise * 0.5 * 1.5;
}

float smoothRing(vec2 uv, float time) {
  float angle = atan(uv.y, uv.x);
  if (angle < 0.0) angle += 2.0 * PI;
  vec2 noiseCoord = vec2(angle / (2.0 * PI), time * 0.1) * 6.0;
  float noise = (noise2D(noiseCoord) - 0.5) * 8.0;
  return 0.9 + noise * 0.3;
}

void main() {
  vec2 uv = vUv * 2.0 - 1.0;
  float radius = length(uv);
  float theta = atan(uv.y, uv.x);
  if (theta < 0.0) theta += 2.0 * PI;

  vec4 color = vec4(1.0, 1.0, 1.0, 1.0);

  float originalCenters[7] = float[7](0.0, 0.5 * PI, 1.0 * PI, 1.5 * PI, 2.0 * PI, 2.5 * PI, 3.0 * PI);
  float centers[7];
  for (int i = 0; i < 7; i++) {
    centers[i] = originalCenters[i] + 0.5 * sin(uTime / 20.0 + uOffsets[i]);
  }

  float a, b;
  vec4 ovalColor;
  for (int i = 0; i < 7; i++) {
    float noise = texture(uPerlinTexture, vec2(mod(centers[i] + uTime * 0.05, 1.0), 0.5)).r;
    a = noise * 1.5;
    b = noise * 4.5;
    bool reverseGradient = (i % 2 == 1);
    float distTheta = abs(theta - centers[i]);
    if (distTheta > PI) distTheta = 2.0 * PI - distTheta;
    float distRadius = radius;
    float softness = 0.4;
    if (drawOval(vec2(distTheta, distRadius), vec2(0.0, 0.0), a, b, reverseGradient, softness, ovalColor)) {
      color.rgb = mix(color.rgb, ovalColor.rgb, ovalColor.a);
      color.a = max(color.a, ovalColor.a);
    }
  }

  float ringRadius1 = sharpRing(uv, theta, uTime);
  float ringRadius2 = smoothRing(uv, uTime);
  float ringAlpha1 = (radius >= ringRadius1) ? 0.3 : 0.0;
  float ringAlpha2 = smoothstep(ringRadius2 - 0.05, ringRadius2 + 0.05, radius) * 0.25;
  float totalRingAlpha = max(ringAlpha1, ringAlpha2);
  vec3 ringColor = vec3(1.0);
  color.rgb = 1.0 - (1.0 - color.rgb) * (1.0 - ringColor * totalRingAlpha);

  // Color ramp: black → uColor1 → uColor2 → white
  vec3 c1 = vec3(0.0, 0.0, 0.0);
  vec3 c2 = uColor1;
  vec3 c3 = uColor2;
  vec3 c4 = vec3(1.0, 1.0, 1.0);
  float luminance = color.r;
  color.rgb = colorRamp(luminance, c1, c2, c3, c4);

  // Hard circular alpha cutoff so the orb is a defined disc, not a square.
  // Soft 2-pixel feather on the edge.
  float discAlpha = 1.0 - smoothstep(0.96, 1.00, radius);
  outColor = vec4(color.rgb, discAlpha);
}`;

// State color pairs — Adjutant amber palette (sRGB hex; gamma-corrected on upload)
const STATE_COLORS = {
  idle:          ["#7a5c20", "#b8893a"],
  listening:     ["#c9941f", "#ffd47a"],
  speaking_user: ["#3a7a9c", "#88c5e0"],
  thinking:      ["#c95028", "#ff8a4a"],
  speaking:      ["#e0a527", "#ffeaa0"],
  error:         ["#9c2828", "#e26060"],
  muted:         ["#3a3f47", "#6b7480"],
};

const QUAD = new Float32Array([-1, 1, -1, -1, 1, 1, 1, -1]);
const POSITION_LOC = 0;

export function initVoiceOrb(canvas) {
  const gl = canvas.getContext("webgl2", { depth: false, stencil: false, premultipliedAlpha: false, antialias: true });
  if (!gl) {
    console.warn("[orb] WebGL2 unavailable, orb disabled");
    return null;
  }

  // Compile shaders
  function compile(type, source) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, source);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.error("[orb] shader compile error:", gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }
  const vs = compile(gl.VERTEX_SHADER,   VERT);
  const fs = compile(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;

  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.bindAttribLocation(program, POSITION_LOC, "position");
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.error("[orb] program link error:", gl.getProgramInfoLog(program));
    return null;
  }
  gl.useProgram(program);

  // Quad
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, QUAD, gl.STATIC_DRAW);
  gl.vertexAttribPointer(POSITION_LOC, 2, gl.FLOAT, false, 0, 0);
  gl.enableVertexAttribArray(POSITION_LOC);

  // Procedural Perlin-style noise texture (256x1, R channel) — replaces the
  // CDN-hosted PNG ElevenLabs uses, so we stay offline.
  const NOISE_W = 256;
  const noiseData = new Uint8Array(NOISE_W * 4);
  // Smooth value-noise: pick 16 random control points, smooth-interpolate between them
  const ctrl = new Array(16).fill(0).map(() => Math.random());
  for (let i = 0; i < NOISE_W; i++) {
    const t = (i / NOISE_W) * 16;
    const i0 = Math.floor(t) % 16;
    const i1 = (i0 + 1) % 16;
    const f = t - Math.floor(t);
    const u = f * f * (3 - 2 * f);
    const v = ctrl[i0] * (1 - u) + ctrl[i1] * u;
    const byte = Math.round(v * 255);
    noiseData[i * 4 + 0] = byte;
    noiseData[i * 4 + 1] = byte;
    noiseData[i * 4 + 2] = byte;
    noiseData[i * 4 + 3] = 255;
  }
  const tex = gl.createTexture();
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, NOISE_W, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, noiseData);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S,     gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T,     gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

  // Uniform locations
  const uTime          = gl.getUniformLocation(program, "uTime");
  const uOffsets       = gl.getUniformLocation(program, "uOffsets");
  const uColor1        = gl.getUniformLocation(program, "uColor1");
  const uColor2        = gl.getUniformLocation(program, "uColor2");
  const uPerlinTexture = gl.getUniformLocation(program, "uPerlinTexture");
  const uInputVolume   = gl.getUniformLocation(program, "uInputVolume");
  const uOutputVolume  = gl.getUniformLocation(program, "uOutputVolume");

  // Random per-oval phase offsets (matches ElevenLabs)
  const offsets = new Float32Array(7).map(() => Math.random() * Math.PI * 2);
  gl.uniform1fv(uOffsets, offsets);
  gl.uniform1i(uPerlinTexture, 0);

  // Color uploader (sRGB → linear)
  let colorA = [0,0,0], colorB = [0,0,0];
  let targetA = colorA, targetB = colorB;
  function hexToLinear(h) {
    const v = h.charAt(0) === "#" ? h.slice(1) : h;
    const r = parseInt(v.slice(0,2),16) / 255;
    const g = parseInt(v.slice(2,4),16) / 255;
    const b = parseInt(v.slice(4,6),16) / 255;
    return [Math.pow(r, 2.2), Math.pow(g, 2.2), Math.pow(b, 2.2)];
  }
  function setColors(c1Hex, c2Hex) {
    targetA = hexToLinear(c1Hex);
    targetB = hexToLinear(c2Hex);
  }
  function lerpColor(cur, tgt, k) {
    return [cur[0] + (tgt[0] - cur[0]) * k,
            cur[1] + (tgt[1] - cur[1]) * k,
            cur[2] + (tgt[2] - cur[2]) * k];
  }
  setColors(...STATE_COLORS.idle);
  colorA = targetA.slice();
  colorB = targetB.slice();
  gl.uniform3fv(uColor1, colorA);
  gl.uniform3fv(uColor2, colorB);

  // Resize handling
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  function resize() {
    const rect = canvas.getBoundingClientRect();
    const cap = 512;
    const w = Math.min(cap, Math.round(rect.width  * DPR));
    const h = Math.min(cap, Math.round(rect.height * DPR));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width  = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  }
  resize();
  window.addEventListener("resize", resize);
  if ("ResizeObserver" in window) new ResizeObserver(resize).observe(canvas);

  // Time + speed (ElevenLabs' formula: speed ratchets up under load, decays slowly)
  let timeAccum = 0;
  let lastNow = performance.now();
  let speed = 0.5;
  let targetSpeed = 0.5;
  let inputVol = 0;
  let outputVol = 0;

  function frame(now) {
    if (!gl) return;
    const dt = Math.min(0.1, (now - lastNow) / 1000);
    lastNow = now;

    // Speed ramp — ElevenLabs uses speed = 0.2 + (1 - (out-1)^2) * 1.8
    const drive = Math.max(inputVol, outputVol);
    targetSpeed = 0.2 + (1 - Math.pow(drive - 1, 2)) * 1.8;
    if (targetSpeed > speed) speed = targetSpeed;
    else                     speed += (targetSpeed - speed) * 0.04;

    timeAccum += dt * speed;

    // Smooth color crossfade (instant swap reads jarring on hard color changes)
    colorA = lerpColor(colorA, targetA, 0.08);
    colorB = lerpColor(colorB, targetB, 0.08);

    gl.uniform1f(uTime, timeAccum);
    gl.uniform1f(uInputVolume,  inputVol);
    gl.uniform1f(uOutputVolume, outputVol);
    gl.uniform3fv(uColor1, colorA);
    gl.uniform3fv(uColor2, colorB);

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    raf = requestAnimationFrame(frame);
  }
  let raf = requestAnimationFrame(frame);

  return {
    setState(name) {
      const c = STATE_COLORS[name] || STATE_COLORS.idle;
      setColors(c[0], c[1]);
    },
    updateVolume(input, output) {
      inputVol  = Math.max(0, Math.min(1, input  || 0));
      outputVol = Math.max(0, Math.min(1, output || 0));
    },
    dispose() {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    },
  };
}
