import Icon from '../components/Icon';

const cards = [
  ['target', 'Our Mission', 'To promote gender-inclusive education through accessible and practical AI tools.'],
  ['settings', 'Our Approach', 'Combining research-based frameworks, natural language processing, and human-centred design.'],
  ['people', 'A More Inclusive Future', 'We believe that more inclusive teaching materials help create a fairer and more equitable future for everyone.'],
];

export default function AboutPage() {
  return (
    <div className="about page-width">
      <p className="eyebrow">About</p>
      <h1>About CARE</h1>
      <p className="lead">Using AI to support more inclusive and equitable education.</p>
      <p className="about-intro">
        CARE (Content Analysis for Responsible Education) is an AI-powered tool
        designed to help educators identify potential gender inclusivity concerns
        in their teaching materials. Our goal is to support the creation of fair,
        respectful and inclusive learning environments for all students.
      </p>
      <div className="about-grid">
        {cards.map(([icon, title, text]) => (
          <article key={title}>
            <Icon name={icon} size={33} />
            <div><h2>{title}</h2><p>{text}</p></div>
          </article>
        ))}
      </div>
    </div>
  );
}
