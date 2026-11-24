import type { Person } from '@/data/companyData';

interface PersonAvatarProps {
  person: Pick<Person, 'initials' | 'avatarColor' | 'name'>;
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
  ring?: boolean;
}

const sizeMap = {
  xs: { cls: 'w-6 h-6 text-[9px]' },
  sm: { cls: 'w-7 h-7 text-[10px]' },
  md: { cls: 'w-9 h-9 text-[11px]' },
  lg: { cls: 'w-12 h-12 text-[14px]' },
  xl: { cls: 'w-20 h-20 text-[22px]' },
};

// Darken a hex color for gradient end - simple HSL shift
function darken(hex: string): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  const f = (c: number) => Math.max(0, Math.round(c * 0.7)).toString(16).padStart(2, '0');
  return `#${f(r)}${f(g)}${f(b)}`;
}

export default function PersonAvatar({ person, size = 'md', ring = false }: PersonAvatarProps) {
  const s = sizeMap[size];
  const start = person.avatarColor;
  const end = darken(person.avatarColor);

  return (
    <div
      title={person.name}
      className={`${s.cls} rounded-full flex items-center justify-center text-white font-semibold shrink-0 ${
        ring ? 'ring-2 ring-white shadow-sm' : ''
      }`}
      style={{
        background: `linear-gradient(135deg, ${start} 0%, ${end} 100%)`,
        fontFamily: 'var(--font-display)',
      }}
    >
      {person.initials}
    </div>
  );
}
