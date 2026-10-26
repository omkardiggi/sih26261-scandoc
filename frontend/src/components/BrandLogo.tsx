import React, { useState, useRef, useEffect } from "react";

interface BrandLogoProps {
  className?: string;
  imgClassName?: string;
  videoSrc?: string;
  posterSrc?: string;
  alt?: string;
  style?: React.CSSProperties;
  size?: number | string;
}

export function BrandLogo({
  className = "",
  imgClassName = "brand-logo-img",
  videoSrc = "/logo.mp4",
  posterSrc = "/logo.png",
  alt = "Docura emblem",
  style,
  size
}: BrandLogoProps) {
  const [videoAvailable, setVideoAvailable] = useState<boolean>(true);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    // Attempt auto-playback gracefully respecting browser policies
    if (videoRef.current && videoAvailable) {
      videoRef.current.play().catch(() => {
        // Autoplay policy prevented playback or file not yet available;
        // keep poster intact without console crash
      });
    }
  }, [videoAvailable]);

  const customStyle: React.CSSProperties = {
    ...(size ? { width: size, height: size } : {}),
    ...style
  };

  return (
    <span className={`brand-logo-frame ${className}`.trim()} style={customStyle}>
      {videoAvailable ? (
        <video
          ref={videoRef}
          className={imgClassName}
          autoPlay
          loop
          muted
          playsInline
          poster={posterSrc}
          onError={() => setVideoAvailable(false)}
          aria-label={alt}
        >
          <source src={videoSrc} type="video/mp4" />
          <source src="/logo.webm" type="video/webm" />
          {/* Native HTML fallback for engines without video support */}
          <img
            src={posterSrc}
            alt={alt}
            className={imgClassName}
            onError={(e) => {
              if (e.currentTarget.src.endsWith(".png")) {
                e.currentTarget.src = "/logo.jpg";
              }
            }}
          />
        </video>
      ) : (
        <img
          src={posterSrc}
          alt={alt}
          className={imgClassName}
          onError={(e) => {
            if (e.currentTarget.src.endsWith(".png")) {
              e.currentTarget.src = "/logo.jpg";
            }
          }}
        />
      )}
    </span>
  );
}

export default BrandLogo;
