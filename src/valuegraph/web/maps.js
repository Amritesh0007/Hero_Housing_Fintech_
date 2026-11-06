/* Google Maps for KSHETRA — shared by the public site and the signed-in app.
   VGMaps.load() fetches the browser key from /api/v1/config and loads the API once.
   VGMaps.draw(el, points, subject) plots localities coloured by median asking ₹/sqft. */
(() => {
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Muted basemap in the site palette so the price dots carry the colour.
  const STYLE = [
    { elementType: "geometry", stylers: [{ color: "#EFECE4" }] },
    { elementType: "labels.text.fill", stylers: [{ color: "#6B706A" }] },
    { elementType: "labels.text.stroke", stylers: [{ color: "#F8F6F0" }] },
    { featureType: "poi", stylers: [{ visibility: "off" }] },
    { featureType: "transit", stylers: [{ visibility: "off" }] },
    { featureType: "road", elementType: "labels.icon", stylers: [{ visibility: "off" }] },
    { featureType: "road", elementType: "geometry", stylers: [{ color: "#FFFFFF" }] },
    { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#E2E6DC" }] },
    { featureType: "water", elementType: "geometry", stylers: [{ color: "#CBD8D0" }] },
    { featureType: "landscape.natural", elementType: "geometry", stylers: [{ color: "#E6EADF" }] },
    { featureType: "administrative", elementType: "geometry.stroke", stylers: [{ color: "#CFCABD" }] },
  ];

  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  function rateColor(r, lo, hi) {
    const t = hi > lo ? Math.min(1, Math.max(0, (r - lo) / (hi - lo))) : 0.5;
    const a = hex(css("--rate-lo") || "#D5DFD2"), b = hex(css("--rate-hi") || "#0B4B3F");
    return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;
  }
  const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");

  let ready = null;
  function load() {
    if (!ready) ready = (async () => {
      const r = await fetch("/api/v1/config");
      const { maps_api_key: key } = r.ok ? await r.json() : {};
      if (!key) throw new Error("No MAPS_API_KEY configured on the server");
      await new Promise((ok, fail) => {
        window.__vgMapsReady = ok; // Google calls this when the API is usable; script onload fires too early
        const s = Object.assign(document.createElement("script"), {
          src: `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=__vgMapsReady`,
          async: true, onerror: () => fail(new Error("Google Maps script failed to load")),
        });
        document.head.append(s);
      });
      await Promise.all([google.maps.importLibrary("maps"), google.maps.importLibrary("marker")]);
      return true;
    })().catch((e) => { console.warn("Google Maps failed to load:", e); return false; });
    return ready;
  }

  // points: [{locality, lat, lon, median_rate_per_sqft, listings, distance_km?}], subject: {lat, lon, label} | null
  async function draw(el, points, subject, { interactive = true } = {}) {
    if (!(await load())) {
      const p = document.createElement("p");
      p.className = "map-fallback";
      p.textContent = "Map unavailable: Google Maps didn't load. Check MAPS_API_KEY in .env.";
      el.replaceChildren(p);
      return null;
    }
    const m = new google.maps.Map(el, {
      styles: STYLE, disableDefaultUI: !interactive, zoomControl: interactive, fullscreenControl: interactive,
      mapTypeControl: false, streetViewControl: false, scrollwheel: false, gestureHandling: interactive ? "cooperative" : "none",
      keyboardShortcuts: interactive, maxZoom: 14, backgroundColor: "#EFECE4",
    });
    const rates = points.map((p) => p.median_rate_per_sqft);
    const lo = Math.min(...rates), hi = Math.max(...rates);
    const bounds = new google.maps.LatLngBounds();
    points.forEach((p) => bounds.extend({ lat: p.lat, lng: p.lon }));
    if (subject) bounds.extend({ lat: subject.lat, lng: subject.lon });
    if (bounds.isEmpty()) { m.setCenter({ lat: 22.5, lng: 79 }); m.setZoom(4); } else m.fitBounds(bounds, 32);
    const dot = (lat, lon, scale, fill, stroke, weight, title) => new google.maps.Marker({
      position: { lat, lng: lon }, map: m, title,
      icon: { path: google.maps.SymbolPath.CIRCLE, scale, fillColor: fill, fillOpacity: 0.92, strokeColor: stroke, strokeWeight: weight },
    });
    points.forEach((p, i) => setTimeout(() => dot(p.lat, p.lon, 4 + Math.min(9, Math.sqrt(p.listings)),
      rateColor(p.median_rate_per_sqft, lo, hi), "#FFFFFF", 1,
      `${p.locality}: ${inr(p.median_rate_per_sqft)}/sqft · ${p.listings} listings${p.distance_km != null ? ` · ${p.distance_km} km` : ""}`),
    REDUCED ? 0 : 150 + i * 25));
    if (subject) dot(subject.lat, subject.lon, 11, css("--blush") || "#EBC8C0", css("--forest") || "#0B4B3F", 3, subject.label);
    return m;
  }

  window.VGMaps = { load, draw, rateColor };
})();
