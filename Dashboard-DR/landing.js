// Mobile nav toggle
const navToggle = document.getElementById("navToggle");
const navLinks = document.getElementById("navLinks");

navToggle.addEventListener("click", () => {
    navLinks.classList.toggle("open");
});

// Close menu after clicking a link (mobile)
navLinks.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => navLinks.classList.remove("open"));
});

// Highlight active nav link based on the section in view
const sections = document.querySelectorAll("section[id], footer[id]");
const links = navLinks.querySelectorAll("a");

const observer = new IntersectionObserver(
    (entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                links.forEach((l) => l.classList.remove("active"));
                const active = navLinks.querySelector(
                    `a[href="#${entry.target.id}"]`
                );
                if (active) active.classList.add("active");
            }
        });
    },
    { threshold: 0.5 }
);

sections.forEach((section) => observer.observe(section));
