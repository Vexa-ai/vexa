"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import Image from "next/image";
import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/base-path";

interface LogoProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  showText?: boolean;
}

const sizeClasses = {
  sm: "h-6 w-6",
  md: "h-8 w-8",
  lg: "h-12 w-12",
};

const textSizeClasses = {
  sm: "text-base",
  md: "text-lg",
  lg: "text-2xl",
};

export function Logo({ className, size = "md", showText = false }: LogoProps) {
  const { theme, systemTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Determine which logo to use based on theme
  // Use dark logo in light mode, light logo in dark mode (inverse)
  const currentTheme = theme === "system" ? systemTheme : theme;
  const logoSrc = currentTheme === "dark"
    ? withBasePath("/icons/vexalight.svg")
    : withBasePath("/icons/vexadark.svg");

  if (!mounted) {
    // Return a placeholder while theme is being determined
    return (
      <div className={cn("flex items-center gap-2", className)}>
        <div className={cn(sizeClasses[size], "bg-muted rounded-lg animate-pulse")} />
        {showText && (
          <span className={cn("font-semibold", textSizeClasses[size])}>Vexa</span>
        )}
      </div>
    );
  }

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <Image
        src={logoSrc}
        alt="Vexa Logo"
        width={size === "sm" ? 24 : size === "md" ? 32 : 48}
        height={size === "sm" ? 24 : size === "md" ? 32 : 48}
        className={cn(sizeClasses[size], "object-contain")}
        priority
      />
      {showText && (
        <span className={cn("font-semibold", textSizeClasses[size])}>Vexa</span>
      )}
    </div>
  );
}

