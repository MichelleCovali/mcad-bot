export function MCADHeader({ homeUrl = "/", children }) {
  return <div role="banner" className="explorer-header">
    <div className="explorer-header-inner">
      <a className="explorer-brand" href={homeUrl} aria-label="MCAD home">
        <span aria-hidden="true" className="explorer-brand-name">MCAD</span>
        <span className="explorer-brand-caption">Maritime Cyber Attack Database</span>
      </a>
      <nav className="explorer-header-nav" aria-label="Main navigation">{children}</nav>
    </div>
  </div>;
}
