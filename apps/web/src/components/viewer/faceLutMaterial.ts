/**
 * One-draw-call face highlighting (IMPLEMENTATION_BLUEPRINT §6.1):
 * MeshStandardMaterial (studio IBL shading kept) + onBeforeCompile LUT lookup by the per-vertex
 * `_FACE_ID` attribute baked into the GLB (three.js exposes it as `_face_id`).
 */

import * as THREE from "three";

import { LUT_CAPACITY, LUT_SIZE } from "@/lib/lut";

export const LUT_PROGRAM_KEY = "revgate-face-lut-v1";
export const GHOST_ALPHA = 0.16;

const FRAGMENT_LUT = /* glsl */ `#include <color_fragment>
int fid = int(vFaceId + 0.5);
vec2 lutUv = (vec2(float(fid & 127), float(fid >> 7)) + 0.5) / 128.0;
vec4 lut = texture2D(uFaceLut, lutUv);
if (lut.a > 0.01) {
  diffuseColor.rgb = mix(diffuseColor.rgb, lut.rgb, lut.a);
}
if (uGhost > 0.5 && lut.a <= 0.01) {
  diffuseColor.a *= ${GHOST_ALPHA.toFixed(2)};
}`;

export function createLutTexture(): THREE.DataTexture {
  const tex = new THREE.DataTexture(new Uint8Array(LUT_CAPACITY * 4), LUT_SIZE, LUT_SIZE, THREE.RGBAFormat, THREE.UnsignedByteType);
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  tex.flipY = false;
  tex.needsUpdate = true;
  return tex;
}

export function writeLut(tex: THREE.DataTexture, data: Uint8Array): void {
  (tex.image.data as Uint8Array).set(data);
  tex.needsUpdate = true;
}

export interface LutUniforms {
  uFaceLut: { value: THREE.DataTexture };
  uGhost: { value: number };
}

export function createFaceLutMaterial(lut: THREE.DataTexture): THREE.MeshStandardMaterial {
  const material = new THREE.MeshStandardMaterial({ vertexColors: true, metalness: 0.15, roughness: 0.55, side: THREE.DoubleSide });
  const uniforms: LutUniforms = { uFaceLut: { value: lut }, uGhost: { value: 0 } };
  material.userData.lutUniforms = uniforms;
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uFaceLut = uniforms.uFaceLut;
    shader.uniforms.uGhost = uniforms.uGhost;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float _face_id;\nvarying float vFaceId;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFaceId = _face_id;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform sampler2D uFaceLut;\nuniform float uGhost;\nvarying float vFaceId;")
      .replace("#include <color_fragment>", FRAGMENT_LUT);
  };
  material.customProgramCacheKey = () => LUT_PROGRAM_KEY;
  return material;
}

/** Ghost mode: unchanged faces at alpha 0.16 in the transparent pass (depthWrite off). */
export function setGhost(material: THREE.MeshStandardMaterial, ghost: boolean): void {
  (material.userData.lutUniforms as LutUniforms).uGhost.value = ghost ? 1 : 0;
  material.transparent = ghost;
  material.depthWrite = !ghost;
  material.needsUpdate = true;
}

/** O(1) picking: the face hit -> its first vertex -> _face_id attribute. */
export function faceIdOfHit(geometry: THREE.BufferGeometry, vertexA: number): number {
  return Math.round((geometry.getAttribute("_face_id") as THREE.BufferAttribute).getX(vertexA));
}
