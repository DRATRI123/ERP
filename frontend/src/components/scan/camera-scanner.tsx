"use client";

import { CameraIcon, CameraOffIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

interface Detector { detect: (src: HTMLVideoElement) => Promise<{ rawValue: string }[]> }
type DetectorCtor = new (o: { formats: string[] }) => Detector;

const FORMATS = ["ean_13", "ean_8", "code_128", "code_39", "upc_a", "upc_e", "qr_code", "itf"];

export function cameraScanSupported(): boolean {
  return typeof window !== "undefined" && "BarcodeDetector" in window && !!navigator.mediaDevices?.getUserMedia;
}

/** Phone / laptop camera barcode reader. The same code is ignored for 1.5 s so one item isn't counted twice. */
export function CameraScanner({ onCode }: { onCode: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [on, setOn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cb = useRef(onCode);
  useEffect(() => { cb.current = onCode; }, [onCode]);

  useEffect(() => {
    if (!on) return;
    let stream: MediaStream | null = null;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const last = { code: "", at: 0 };
    (async () => {
      try {
        const Ctor = (window as unknown as { BarcodeDetector: DetectorCtor }).BarcodeDetector;
        const detector = new Ctor({ formats: FORMATS });
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
        const v = video.current;
        if (!v || stop) return;
        v.srcObject = stream;
        await v.play();
        const tick = async () => {
          if (stop) return;
          try {
            const found = await detector.detect(v);
            const code = found[0]?.rawValue;
            const now = Date.now();
            if (code && (code !== last.code || now - last.at > 1500)) {
              last.code = code; last.at = now;
              navigator.vibrate?.(60);
              cb.current(code);
            }
          } catch { /* frame not ready */ }
          timer = setTimeout(tick, 250);
        };
        tick();
      } catch (e) {
        setError(e instanceof Error && e.name === "NotAllowedError" ? "Camera permission was refused." : "Could not open the camera.");
        setOn(false);
      }
    })();
    return () => { stop = true; clearTimeout(timer); stream?.getTracks().forEach((t) => t.stop()); };
  }, [on]);

  if (!cameraScanSupported()) {
    return <p className="text-xs text-muted-foreground">This browser cannot read barcodes from the camera (use Chrome on Android). Type the code or use a USB / Bluetooth scanner in the box.</p>;
  }
  return (
    <div className="space-y-2">
      <Button type="button" variant={on ? "secondary" : "outline"} onClick={() => { setError(null); setOn((v) => !v); }}>
        {on ? <><CameraOffIcon /> Stop camera</> : <><CameraIcon /> Scan with camera</>}
      </Button>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {on && <video ref={video} muted playsInline className="aspect-video w-full max-w-md rounded-lg border bg-black object-cover" />}
    </div>
  );
}
