"use client";

/**
 * R3F 3D diff viewer (IMPLEMENTATION_BLUEPRINT §6):
 *  - base / target lod0 GLB, each ONE mesh + ONE draw call, highlighted through a 128x128 face LUT
 *  - target placed in the base frame with transform_4x4 (row-major, Target -> Base)
 *  - delta_added / delta_removed overlays, ghost mode, Before/After/Overlay
 *  - picking: hit.face.a -> attributes._face_id.getX(a)  (O(1))
 *  - OrbitControls with up = [0, 0, 1]; change focus = 450 ms easeOutCubic, cancelled by user input
 *  - drei <Html> pins with occlusion (opacity 0.25 when behind geometry)
 */

import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, type ThreeEvent, useFrame, useLoader, useThree } from "@react-three/fiber";
import { Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { OrbitControls as OrbitControlsImpl } from "three/examples/jsm/controls/OrbitControls.js";

import type { V3 } from "@/lib/vec";

import { createFaceLutMaterial, createLutTexture, faceIdOfHit, setGhost, writeLut } from "./faceLutMaterial";

THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

export type ViewMode = "BASE" | "TARGET" | "OVERLAY";
export type Side = "BASE" | "TARGET";

export interface FocusRequest {
  target: V3;
  dir: V3;
  radius: number;
  nonce: number;
}

export interface ViewerPin {
  id: string;
  xyz: V3;
  label: string;
  resolved: boolean;
}

export interface PickEvent {
  side: Side;
  faceId: number;
  point: V3;
}

export interface DiffViewerProps {
  baseUrl: string | null;
  targetUrl: string | null;
  deltaAddedUrl?: string | null;
  deltaRemovedUrl?: string | null;
  transform: number[];
  baseLut: Uint8Array;
  targetLut: Uint8Array;
  mode: ViewMode;
  ghost: boolean;
  showAdded: boolean;
  showRemoved: boolean;
  focus: FocusRequest | null;
  pins: ViewerPin[];
  onPick?: (e: PickEvent) => void;
}

declare global {
  interface Window {
    __REVGATE_VIEWER__?: { ready: boolean; calls: number; triangles: number; mode: ViewMode; ghost: boolean; lastPick?: PickEvent };
  }
}

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);
const FOCUS_MS = 450;
const noRaycast = () => null;

function StudioEnvironment() {
  const { gl, scene } = useThree();
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = env;
    return () => {
      scene.environment = null;
      env.dispose();
      pmrem.dispose();
    };
  }, [gl, scene]);
  return null;
}

function useMeshGeometry(url: string): THREE.BufferGeometry {
  const gltf = useLoader(GLTFLoader, url);
  return useMemo(() => {
    let geometry: THREE.BufferGeometry | null = null;
    gltf.scene.traverse((o) => {
      if (!geometry && (o as THREE.Mesh).isMesh) geometry = (o as THREE.Mesh).geometry;
    });
    if (!geometry) throw new Error("GLB has no mesh");
    return geometry;
  }, [gltf]);
}

interface ModelProps {
  url: string;
  side: Side;
  lut: Uint8Array;
  visible: boolean;
  ghost: boolean;
  matrix: THREE.Matrix4 | null;
  meshRef: React.RefObject<THREE.Mesh | null>;
  onPick?: (e: PickEvent) => void;
  onGeometry?: (g: THREE.BufferGeometry) => void;
}

function Model({ url, side, lut, visible, ghost, matrix, meshRef, onPick, onGeometry }: ModelProps) {
  const geometry = useMeshGeometry(url);
  const texture = useMemo(() => createLutTexture(), []);
  const material = useMemo(() => createFaceLutMaterial(texture), [texture]);
  useEffect(() => writeLut(texture, lut), [texture, lut]);
  useEffect(() => setGhost(material, ghost), [material, ghost]);
  useEffect(() => () => {
    material.dispose();
    texture.dispose();
  }, [material, texture]);
  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(matrix ?? new THREE.Matrix4());
    mesh.matrixWorldNeedsUpdate = true;
  }, [matrix, meshRef]);
  useEffect(() => onGeometry?.(geometry), [geometry, onGeometry]);

  const click = (e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4 || e.face == null) return; // orbit drag, not a pick
    e.stopPropagation();
    const pick = { side, faceId: faceIdOfHit(geometry, e.face.a), point: [e.point.x, e.point.y, e.point.z] as V3 };
    if (window.__REVGATE_VIEWER__) window.__REVGATE_VIEWER__.lastPick = pick;
    onPick?.(pick);
  };

  return (
    <mesh
      ref={meshRef}
      name={`model-${side}`}
      geometry={geometry}
      material={material}
      visible={visible}
      raycast={visible ? THREE.Mesh.prototype.raycast : noRaycast}
      onClick={click}
    />
  );
}

function DeltaModel({ url, color }: { url: string; color: string }) {
  const geometry = useMeshGeometry(url);
  const material = useMemo(
    () => new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.85, polygonOffset: true, polygonOffsetFactor: -2, depthWrite: false }),
    [color],
  );
  return <mesh geometry={geometry} material={material} raycast={noRaycast} renderOrder={2} />;
}

function CameraRig({ sphere, focus }: { sphere: THREE.Sphere | null; focus: FocusRequest | null }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const controls = useThree((s) => s.controls) as unknown as OrbitControlsImpl | null;
  const tween = useRef<{ t0: number; fromPos: THREE.Vector3; toPos: THREE.Vector3; fromTarget: THREE.Vector3; toTarget: THREE.Vector3 } | null>(null);
  const fitted = useRef(false);

  useEffect(() => {
    if (!sphere || !controls || fitted.current) return;
    fitted.current = true;
    const r = Math.max(sphere.radius, 1);
    controls.target.copy(sphere.center);
    camera.position.copy(sphere.center).add(new THREE.Vector3(1, -1.3, 0.9).normalize().multiplyScalar(r * 2.6));
    camera.near = r / 200;
    camera.far = r * 200;
    camera.updateProjectionMatrix();
    controls.update();
  }, [sphere, controls, camera]);

  useEffect(() => {
    if (!controls) return;
    const cancel = () => (tween.current = null); // user takes over -> abort the animation
    controls.addEventListener("start", cancel);
    return () => controls.removeEventListener("start", cancel);
  }, [controls]);

  useEffect(() => {
    if (!focus || !controls) return;
    const to = new THREE.Vector3(...focus.target);
    const dir = new THREE.Vector3(...focus.dir).normalize();
    tween.current = {
      t0: performance.now(),
      fromPos: camera.position.clone(),
      toPos: to.clone().add(dir.multiplyScalar(focus.radius * 2.8)),
      fromTarget: controls.target.clone(),
      toTarget: to,
    };
  }, [focus, controls, camera]);

  useFrame(() => {
    const tw = tween.current;
    if (!tw || !controls) return;
    const t = Math.min(1, (performance.now() - tw.t0) / FOCUS_MS);
    const k = easeOutCubic(t);
    camera.position.lerpVectors(tw.fromPos, tw.toPos, k);
    controls.target.lerpVectors(tw.fromTarget, tw.toTarget, k);
    controls.update();
    if (t >= 1) tween.current = null;
  });
  return null;
}

function PinMarker({ pin, occluders }: { pin: ViewerPin; occluders: React.RefObject<THREE.Object3D | null>[] }) {
  const [hidden, setHidden] = useState(false);
  return (
    // labels must never swallow clicks: picking / pinning the face underneath goes straight to the canvas
    <Html
      position={pin.xyz}
      occlude={occluders as React.RefObject<THREE.Object3D>[]}
      onOcclude={setHidden}
      zIndexRange={[30, 0]}
      center
      wrapperClass="pointer-events-none"
      style={{ pointerEvents: "none" }}
    >
      <div
        data-testid="pin-marker"
        data-occluded={hidden ? "true" : "false"}
        style={{ opacity: hidden ? 0.25 : 1, transition: "opacity 120ms" }}
        className={`pointer-events-none select-none rounded-full px-2 py-0.5 text-xs font-semibold text-white shadow ${pin.resolved ? "bg-slate-500" : "bg-fuchsia-600"}`}
      >
        📍 {pin.label}
      </div>
    </Html>
  );
}

function Scene(props: DiffViewerProps) {
  const baseRef = useRef<THREE.Mesh>(null);
  const targetRef = useRef<THREE.Mesh>(null);
  const [sphere, setSphere] = useState<THREE.Sphere | null>(null);
  const matrix = useMemo(() => new THREE.Matrix4().fromArray(props.transform).transpose(), [props.transform]); // row-major -> three
  const onBaseGeometry = useMemo(
    () => (g: THREE.BufferGeometry) => {
      g.computeBoundingSphere();
      if (g.boundingSphere) setSphere(g.boundingSphere.clone());
    },
    [],
  );

  useFrame(({ gl }) => {
    window.__REVGATE_VIEWER__ = {
      ...(window.__REVGATE_VIEWER__ ?? {}),
      ready: sphere !== null,
      calls: gl.info.render.calls,
      triangles: gl.info.render.triangles,
      mode: props.mode,
      ghost: props.ghost,
    };
  });

  const showBase = props.mode !== "TARGET";
  const showTarget = props.mode !== "BASE";
  return (
    <>
      {props.baseUrl && (
        <Model url={props.baseUrl} side="BASE" lut={props.baseLut} visible={showBase} ghost={props.ghost} matrix={null}
               meshRef={baseRef} onPick={props.onPick} onGeometry={onBaseGeometry} />
      )}
      {props.targetUrl && (
        <Model url={props.targetUrl} side="TARGET" lut={props.targetLut} visible={showTarget} ghost={props.ghost} matrix={matrix}
               meshRef={targetRef} onPick={props.onPick} />
      )}
      {props.showAdded && props.deltaAddedUrl && <DeltaModel url={props.deltaAddedUrl} color="#22c55e" />}
      {props.showRemoved && props.deltaRemovedUrl && <DeltaModel url={props.deltaRemovedUrl} color="#ef4444" />}
      {props.pins.map((p) => (
        <PinMarker key={p.id} pin={p} occluders={[baseRef, targetRef]} />
      ))}
      <CameraRig sphere={sphere} focus={props.focus} />
    </>
  );
}

export default function DiffViewer(props: DiffViewerProps) {
  return (
    <Canvas
      data-testid="viewer-canvas"
      camera={{ fov: 40, near: 0.1, far: 100000, position: [180, -220, 160], up: [0, 0, 1] }}
      dpr={[1, 2]}
      gl={{ antialias: true }}
      onCreated={({ gl }) => gl.setClearColor("#eef1f5")}
    >
      <StudioEnvironment />
      <ambientLight intensity={0.25} />
      <directionalLight position={[300, -400, 500]} intensity={1.0} />
      <directionalLight position={[-400, 300, 200]} intensity={0.35} />
      <Suspense fallback={null}>
        <Scene {...props} />
      </Suspense>
      <OrbitControls makeDefault enableDamping dampingFactor={0.15} />
    </Canvas>
  );
}
