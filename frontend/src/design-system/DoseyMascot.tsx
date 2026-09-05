import doseyCharacterSheet from '../assets/dosey-auth-welcome.png'

export function DoseyMascot({
  variant,
}: {
  variant: 'welcome' | 'header' | 'nav' | 'chat'
}) {
  return (
    <span className={`dosey-mascot dosey-mascot--${variant}`} aria-hidden="true">
      <img src={doseyCharacterSheet} alt="" />
    </span>
  )
}
