import Image from "next/image";
import { Monitor, Users, Video } from "lucide-react";
import { getPlatformConfig } from "@/types/vexa";

const LUCIDE_ICONS = { video: Video, users: Users, monitor: Monitor } as const;

interface PlatformIconProps {
  platform: string;
  /** Pixel size of the logo; the lucide fallback is sized by className. */
  size?: number;
  className?: string;
}

/**
 * The one place a platform becomes a picture. Platforms without a logo asset
 * render their lucide icon, so a new platform is generic rather than mislabelled.
 */
export function PlatformIcon({ platform, size = 20, className }: PlatformIconProps) {
  const config = getPlatformConfig(platform);

  if (config.iconSrc) {
    return (
      <Image
        src={config.iconSrc}
        alt={config.name}
        width={size}
        height={size}
        className={className}
      />
    );
  }

  const Icon = LUCIDE_ICONS[config.icon];
  return <Icon aria-label={config.name} className={className} />;
}
