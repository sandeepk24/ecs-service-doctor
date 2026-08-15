export function EcsLogo({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      role="img"
      aria-label="Amazon ECS"
    >
      <defs>
        <linearGradient id="ecs-hex" x1="12" y1="4" x2="52" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#FFB84D" />
          <stop offset="1" stopColor="#E87722" />
        </linearGradient>
      </defs>
      <path
        fill="url(#ecs-hex)"
        d="M32 4 56 18v28L32 60 8 46V18L32 4Z"
      />
      <path
        fill="#fff"
        d="M32 14.2 44.8 21.5v14.8L32 43.6 19.2 36.3V21.5L32 14.2Zm0 3.4-8.6 4.9v9.8L32 37l8.6-4.7v-9.8L32 17.6Z"
      />
      <path
        fill="#232F3E"
        opacity="0.22"
        d="M32 22.8 40.6 27.7 32 32.5l-8.6-4.8L32 22.8Z"
      />
      <path fill="#fff" d="M23.4 27.7 32 32.5v9.7l-8.6-4.9V27.7Z" opacity="0.92" />
      <path fill="#E8EEF2" d="M40.6 27.7V37.3L32 42.2v-9.7l8.6-4.8Z" />
    </svg>
  );
}
