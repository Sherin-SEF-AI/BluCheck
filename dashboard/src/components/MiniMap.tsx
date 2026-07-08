// Small OpenStreetMap embed for the capture location. No API key required.
export default function MiniMap({ lat, lon }: { lat: number; lon: number }) {
  const d = 0.004;
  const bbox = `${lon - d},${lat - d},${lon + d},${lat + d}`;
  const src = `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat},${lon}`;
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden", maxWidth: 360 }}>
      <iframe
        title="capture-location"
        src={src}
        style={{ width: "100%", height: 200, border: 0 }}
        loading="lazy"
      />
      <div className="mono" style={{ padding: "8px 12px", fontSize: 12 }}>
        {lat.toFixed(6)}, {lon.toFixed(6)}
      </div>
    </div>
  );
}
