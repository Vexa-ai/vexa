"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import Image from "next/image";
import { cn } from "@/lib/utils";

interface LogoProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  showText?: boolean;
}

const sizeClasses = {
  sm: "h-5 w-5 rounded-[5px]",
  md: "h-[26px] w-[26px] rounded-[7px]",
  lg: "h-10 w-10 rounded-[9px]",
};

const textSizeClasses = {
  sm: "text-[13px]",
  md: "text-[15px]",
  lg: "text-xl",
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
  const logoSrc = currentTheme === "dark" ? "/icons/vexalight.svg" : "/icons/vexadark.svg";

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
        <span className={cn("font-semibold tracking-[-0.01em] text-foreground", textSizeClasses[size])}>vexa</span>
      )}
    </div>
  );
}

