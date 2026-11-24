import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ops Copilot",
  description: "Support inbox with an assistant that investigates tickets and proposes fixes.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="bg-canvas text-ink">{children}</body>
    </html>
  );
}
