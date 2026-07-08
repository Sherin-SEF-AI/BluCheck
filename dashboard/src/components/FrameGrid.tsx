"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { getFrameUrl, getAnnotatedFrame, type CaptureDetail, type FrameOut } from "@/lib/api";
import { fmtTimeIST } from "@/lib/time";

const fmtTs = fmtTimeIST;

export default function FrameGrid({
  inspectionId,
  capture,
  onPin,
  onOpen,
}: {
  inspectionId: string;
  capture: CaptureDetail;
  onPin?: (frame: FrameOut, url: string) => void;
  onOpen?: (frameId: string) => void;
}) {
  const [index, setIndex] = useState<number | null>(null);
  const [fullUrl, setFullUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  // On-demand VLM issue detection overlay.
  const [annotatedUrl, setAnnotatedUrl] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [detectErr, setDetectErr] = useState<string | null>(null);
  // The annotated frame is a blob object URL; revoke the previous one whenever it changes or the
  // component unmounts so we do not leak memory (the cleanup runs with the prior value).
  useEffect(() => {
    return () => { if (annotatedUrl) URL.revokeObjectURL(annotatedUrl); };
  }, [annotatedUrl]);
  // Cache of full-res signed URLs so hover-preloaded frames open instantly.
  const urlCache = useRef<Record<string, string>>({});
  const [showAll, setShowAll] = useState(false);

  // Show the model/selection-chosen frames first; let the reviewer expand to all.
  const hasSelected = capture.frames.some((f) => f.selected);
  const frames = !hasSelected || showAll ? capture.frames : capture.frames.filter((f) => f.selected);

  const fetchUrl = useCallback(
    async (f: FrameOut): Promise<string> => {
      if (urlCache.current[f.id]) return urlCache.current[f.id];
      const res = await getFrameUrl(inspectionId, f.id);
      urlCache.current[f.id] = res.url;
      return res.url;
    },
    [inspectionId]
  );

  const loadFrame = useCallback(
    async (i: number) => {
      const f = frames[i];
      if (!f) return;
      setScale(1);
      setOffset({ x: 0, y: 0 });
      setAnnotatedUrl(null);
      setDetectErr(null);
      if (urlCache.current[f.id]) {
        setFullUrl(urlCache.current[f.id]);
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        setFullUrl(await fetchUrl(f));
      } finally {
        setLoading(false);
      }
    },
    [frames, fetchUrl]
  );

  function open(i: number) {
    setIndex(i);
    loadFrame(i);
    const f = frames[i];
    if (f) onOpen?.(f.id);
  }
  const close = useCallback(() => {
    setIndex(null);
    setFullUrl(null);
  }, []);

  const step = useCallback(
    (delta: number) => {
      setIndex((cur) => {
        if (cur === null) return cur;
        const next = Math.min(frames.length - 1, Math.max(0, cur + delta));
        if (next !== cur) loadFrame(next);
        return next;
      });
    },
    [frames.length, loadFrame]
  );

  useEffect(() => {
    if (index === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "+" || e.key === "=") setScale((s) => Math.min(6, s + 0.5));
      else if (e.key === "-") setScale((s) => Math.max(1, s - 0.5));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, step, close]);

  return (
    <>
      <div className="frame-grid">
        {frames.map((f, i) => (
          <div
            key={f.id}
            className="frame"
            onClick={() => open(i)}
            onMouseEnter={() => { fetchUrl(f).catch(() => undefined); }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={f.thumb_url} alt={`frame ${f.seq}`} loading="lazy" />
            <div className="overlay">
              <div>{fmtTs(f.absolute_ts_utc)} IST</div>
              <div>
                {f.gps_lat != null && f.gps_lon != null
                  ? `${f.gps_lat.toFixed(5)}, ${f.gps_lon.toFixed(5)}`
                  : "no gps"}
              </div>
            </div>
            {onPin ? (
              <button
                className="pin-btn"
                onClick={async (e) => { e.stopPropagation(); onPin(f, await fetchUrl(f)); }}
                title="Pin to compare"
              >
                pin
              </button>
            ) : null}
          </div>
        ))}
        {frames.length === 0 ? <div className="dim">No frames extracted yet.</div> : null}
      </div>
      {hasSelected ? (
        <button className="ghost" style={{ marginTop: 8 }} onClick={() => setShowAll((s) => !s)}>
          {showAll
            ? `Show selected only (${capture.frames.filter((f) => f.selected).length})`
            : `Show all ${capture.frames.length} frames`}
        </button>
      ) : null}

      {index !== null ? (
        <div className="lightbox" onClick={close}>
          <button className="lb-nav lb-prev" onClick={(e) => { e.stopPropagation(); step(-1); }} disabled={index === 0}>‹</button>
          <div className="lb-col" onClick={(e) => e.stopPropagation()}>
            <div
              className="lb-stage"
              onWheel={(e) => setScale((s) => Math.min(6, Math.max(1, s + (e.deltaY < 0 ? 0.3 : -0.3))))}
              onMouseDown={(e) => { drag.current = { x: e.clientX - offset.x, y: e.clientY - offset.y }; }}
              onMouseMove={(e) => { if (drag.current) setOffset({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y }); }}
              onMouseUp={() => { drag.current = null; }}
              onMouseLeave={() => { drag.current = null; }}
            >
              {loading ? <div className="dim">Loading full resolution...</div> : null}
              {(annotatedUrl ?? fullUrl) ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={annotatedUrl ?? fullUrl!} alt="full frame" style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`, cursor: scale > 1 ? "grab" : "default" }} draggable={false} />
              ) : null}
              <div className="lb-meta mono">
                {capture.kind} · frame {frames[index].seq}/{frames.length} · {fmtTs(frames[index].absolute_ts_utc)} IST
                &nbsp;·&nbsp;
                {annotatedUrl ? (
                  <button className="ghost" style={{ padding: "2px 8px", fontSize: 11 }} onClick={() => setAnnotatedUrl(null)}>Hide detections</button>
                ) : (
                  <button
                    className="ghost"
                    style={{ padding: "2px 8px", fontSize: 11 }}
                    disabled={detecting}
                    onClick={async () => {
                      setDetecting(true); setDetectErr(null);
                      try { setAnnotatedUrl(await getAnnotatedFrame(inspectionId, frames[index].id)); }
                      catch (e) { setDetectErr(e instanceof Error ? e.message : "detection failed"); }
                      finally { setDetecting(false); }
                    }}
                  >
                    {detecting ? "Detecting..." : "Detect issues"}
                  </button>
                )}
                {detectErr ? <span style={{ color: "var(--danger)" }}> · {detectErr}</span> : null}
              </div>
            </div>
            {/* Filmstrip scrubber */}
            <div className="filmstrip">
              {frames.map((f, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={f.id}
                  src={f.thumb_url}
                  alt={`f${f.seq}`}
                  className={i === index ? "active" : ""}
                  onClick={() => { setIndex(i); loadFrame(i); }}
                />
              ))}
            </div>
          </div>
          <button className="lb-nav lb-next" onClick={(e) => { e.stopPropagation(); step(1); }} disabled={index === frames.length - 1}>›</button>
        </div>
      ) : null}
    </>
  );
}
