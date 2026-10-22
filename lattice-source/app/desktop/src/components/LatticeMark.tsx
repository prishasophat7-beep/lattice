/** The Lattice logo (src-tauri/icons/lattice-logo.svg) in the theme's colours: a knowledge lattice
 * with the found answer in safety yellow. */
export default function LatticeMark({ big = false }: { big?: boolean }) {
  return (
    <svg className={`brand-mark${big ? " big" : ""}`} viewBox="0 0 1024 1024" aria-hidden="true" focusable="false">
      <rect width="1024" height="1024" rx="232" fill="var(--accent)" />
      <g stroke="var(--accent-ink)" strokeWidth="48" strokeLinecap="round" opacity="0.92">
        <path d="M512 212 L812 512 M362 362 L662 662 M212 512 L512 812" />
        <path d="M512 212 L212 512 M662 362 L362 662 M812 512 L512 812" />
      </g>
      <g fill="var(--accent-ink)">
        <circle cx="512" cy="212" r="64" />
        <circle cx="662" cy="362" r="64" />
        <circle cx="812" cy="512" r="64" />
        <circle cx="362" cy="362" r="64" />
        <circle cx="662" cy="662" r="64" />
        <circle cx="212" cy="512" r="64" />
        <circle cx="362" cy="662" r="64" />
        <circle cx="512" cy="812" r="64" />
      </g>
      <circle cx="512" cy="512" r="92" fill="var(--tape)" stroke="var(--accent)" strokeWidth="20" />
    </svg>
  );
}
