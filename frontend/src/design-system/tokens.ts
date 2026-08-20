export const designTokens = {
  color: {
    action: '#0066CC',
    actionPressed: '#0052A3',
    tile: '#0A2A4A',
    canvas: '#F5F7FA',
    surface: '#FFFFFF',
    text: '#17191C',
    textMuted: '#5E6670',
    line: '#DDE2E8',
    muted: '#EDF1F5',
    attention: '#A35200',
    danger: '#B3261E',
    focus: '#66AFFF',
  },
  radius: {
    small: '12px',
    medium: '16px',
    large: '24px',
    pill: '999px',
  },
  space: {
    1: '4px',
    2: '8px',
    3: '12px',
    4: '16px',
    5: '20px',
    6: '24px',
    8: '32px',
  },
  type: {
    family: "'Spoqa Han Sans Neo', system-ui, -apple-system, sans-serif",
    body: '16px',
    bodySmall: '14px',
    caption: '13px',
    title: '28px',
  },
  layout: {
    minWidth: '360px',
    baseWidth: '390px',
    maxWidth: '412px',
    contentPaddingNarrow: '16px',
    contentPadding: '20px',
    bottomNavigationHeight: '72px',
  },
} as const

export type DesignTokens = typeof designTokens
