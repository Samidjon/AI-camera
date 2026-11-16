import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { ServiceWorkerRegister } from "@/components/layout/sw-register";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth/auth-provider";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin", "cyrillic"],
});
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin", "cyrillic"],
});

const THEME_SCRIPT = `try{var t=localStorage.getItem("aci-theme")||"dark";var d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch(e){}`;

export const metadata: Metadata = {
  title: {
    default: "Camera Intelligence",
    template: "%s · Camera Intelligence",
  },
  description: "AI Camera Intelligence — surveillance control center",
  applicationName: "Camera Intelligence",
  appleWebApp: {
    capable: true,
    title: "Camera Intel",
    statusBarStyle: "black-translucent",
  },
  icons: {
    icon: [
      { url: "/favicon-32.png", sizes: "32x32" },
      { url: "/icons/icon-192.png", sizes: "192x192" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  themeColor: "#181d24",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover", // full screen on notched phones when installed
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // Dark by default. The inline script applies the saved theme before the first paint (no flash);
    // suppressHydrationWarning: the class on <html> is set before React hydrates.
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-full">
        <TooltipProvider delayDuration={200}>
          <AuthProvider>{children}</AuthProvider>
          <ServiceWorkerRegister />
        </TooltipProvider>
      </body>
    </html>
  );
}
