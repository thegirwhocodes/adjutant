// Adjutant voice orb — soft amorphous gradient blob with audio-reactive
// surface ripple, FBM noise interior, and amplitude-driven hue mix.
//
// The previous version was a direct port of ElevenLabs' ConvAI Orb shader
// (MIT, Copyright (c) 2025 ElevenLabs — github.com/elevenlabs/packages/
// convai-widget-core/src/orb/OrbShader.frag). It's preserved in git
// history; this rewrite keeps the same module API (initVoiceOrb,
// setState, updateVolume) but renders a different visual: a soft
// gradient cloud disc instead of seven polar ovals + noise rings. The
// audio reactivity (color tones shifting while you speak) is preserved.
//
// Influences: ElevenLabs orb (general structure, gamma correction,
// speed-formula), ChatGPT mobile orb (soft cloud aesthetic, FBM
// vertex displacement). All shader code below is original.

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
uniform vec3 uColor1;
uniform vec3 uColor2;
uniform float uInputVolume;
uniform float uOutputVolume;

in vec2 vUv;
out vec4 outColor;

const float PI = 3.14159265358979323846;

// 2D hash + value noise + FBM (4 octaves)
float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}
float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}
float fbm(vec2 p) {
  float v = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 4; i++) {
    v += amp * vnoise(p);
    p *= 2.03;
    amp *= 0.5;
  }
  return v;
}

void main() {
  vec2 uv = vUv * 2.0 - 1.0;          // -1..1
  float r   = length(uv);
  float ang = atan(uv.y, uv.x);

  float drive = max(uInputVolume, uOutputVolume);
  float t     = uTime;

  // --- Perimeter ripple — irregular squishy edge driven by amplitude ---
  // Unwrap angle into a circle-of-noise so the perimeter wobbles smoothly
  // and seamlessly (no seam at theta = ±PI).
  float n_perim = fbm(vec2(cos(ang) * 1.6 + t * 0.25,
                           sin(ang) * 1.6 + t * 0.25));
  float ripple  = (n_perim - 0.5) * (0.06 + drive * 0.14);
  float discR   = 0.78 + ripple;       // base radius with audio-reactive wobble

  // --- Disc alpha with feathered edge ---
  float disc = 1.0 - smoothstep(discR - 0.06, discR, r);
  if (disc <= 0.0) {
    outColor = vec4(0.0);
    return;
  }

  // --- Interior cloud — multi-octave noise drifting ---
  float cloud = fbm(uv * 1.4 + vec2(t * 0.18, t * -0.12));

  // --- Radial brightness — soft falloff from a slightly-off-center core ---
  vec2 coreOffset = vec2(sin(t * 0.3) * 0.10, cos(t * 0.27) * 0.10);
  float coreD = length(uv - coreOffset);
  float core  = pow(1.0 - clamp(coreD / discR, 0.0, 1.0), 1.6);

  // --- Color blend: lerp uColor1 → uColor2 by core+cloud, with extra
  //     hue lift on amplitude (this is the "tones shift while talking"
  //     feel — the higher the amplitude, the more uColor2 shows).
  float mixT = clamp(core * 0.7 + cloud * 0.5 + drive * 0.25, 0.0, 1.0);
  vec3 base  = mix(uColor1, uColor2, mixT);

  // --- Bright core lift — adds the "glowing center" without a Phong sphere ---
  vec3 lit = base + uColor2 * core * (0.20 + drive * 0.30);

  // --- Soft fresnel rim — defines the edge without a halo ---
  float rim = smoothstep(discR - 0.18, discR, r);
  lit = mix(lit, uColor2 * 0.85, rim * 0.35);

  // --- Subtle inner shadow at the bottom for depth (not Phong, just bias) ---
  float shade = clamp(uv.y * 0.18 + 0.5, 0.0, 1.0);
  lit *= 0.85 + shade * 0.20;

  outColor = vec4(lit, disc);
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
