import type { Metadata } from "next";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "PaperPilot · 科研助手",
  description: "可追溯、抗幻觉的论文阅读与研究工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
