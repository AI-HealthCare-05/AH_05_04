import doseyCharacterSheet from '../assets/dosey-auth-welcome.png'
import doseyHomeHero from '../assets/dosey-home-hero.png'

export function DoseyMascot({
  variant,
}: {
  variant: 'welcome' | 'header' | 'hero' | 'progress' | 'nav' | 'chat'
}) {
  return (
    <span className={`dosey-mascot dosey-mascot--${variant}`} aria-hidden="true">
      <img
        src={variant === 'hero' ? doseyHomeHero : doseyCharacterSheet}
        alt=""
        draggable={false}
      />
    </span>
  )
}
