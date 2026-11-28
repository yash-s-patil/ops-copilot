import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";
import { THEME_COOKIE, themeFrom } from "@/lib/theme";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });
const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: "Ops Copilot",
  description: "Support inbox with an assistant that investigates tickets and proposes fixes.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // The theme is read on the server, so the first paint is already in it.
  const theme = themeFrom((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html
      lang="en"
      data-theme={theme === "system" ? undefined : theme}
      className={`${inter.variable} ${mono.variable}`}
    >
      <body className="bg-canvas text-ink">{children}</body>
    </html>
  );
}
