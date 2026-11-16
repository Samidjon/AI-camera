import type { MetadataRoute } from "next";

/** Installable app (PWA): "Add to Home screen" on phones, "Install app" in desktop browsers. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Camera Intelligence — Control Center",
    short_name: "Camera Intel",
    description:
      "Live cameras, events, edge devices and AI status of your AI Camera Intelligence installation.",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#181d24",
    theme_color: "#181d24",
    categories: ["security", "utilities", "business"],
    icons: [
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      {
        name: "Live",
        url: "/monitoring",
        icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }],
      },
      {
        name: "Events",
        url: "/events",
        icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }],
      },
      {
        name: "Devices",
        url: "/devices",
        icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }],
      },
    ],
  };
}
