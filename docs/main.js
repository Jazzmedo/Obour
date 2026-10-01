// Obour's landing page: the language switch, copy buttons and the scroll story.
// English lives in index.html; Arabic is here, keyed by each element's data-i18n.

const AR = {
  "brand": "عبور",
  "nav.download": "تنزيل",
  "hero.alpha": "ألفا",
  "hero.title": "عبور",
  "hero.means": "من حاسوب إلى آخر، كأنها على جهازك",
  "hero.tagline": "افتح تطبيقات حواسيب لينكس الأخرى كنوافذ عادية على سطح مكتبك.",
  "hero.cta": "احصل على عبور",
  "hero.hint": "مرّر لترى التطبيق وهو يعبر",
  "stage.remote": "حاسوب آخر",
  "stage.local": "سطح مكتبك",
  "s0.h": "تطبيقاتك على حاسوب آخر.",
  "s0.p": "خادم في الخزانة، أو حاسوب في المكتب، أو لابتوب قديم. يأتيك عبور بتطبيقاتها إلى الجهاز الذي أمامك.",
  "s1.h": "يتصل عبر SSH.",
  "s1.p": "بلا سطح مكتب بعيد ولا خادم إضافي: يكفي دخول SSH الذي لديك أصلًا.",
  "s2.h": "يعبر التطبيق إليك.",
  "s2.p": "يصل نافذةً عادية تتحرك وتتغير أحجامها وتُرتَّب مثل التطبيقات المحلية. تطبيقات Wayland تعبر عبر waypipe، وتطبيقات X11 القديمة عبر SSH، ويختار عبور المناسب لك.",
  "s3.h": "ويأتي صوته معه.",
  "s3.p": "يخرج الصوت من سمّاعاتك عبر الاتصال نفسه.",
  "s4.h": "والملفات تعبر في الاتجاهين.",
  "s4.p": "اسحب ملفًا إلى تطبيق بعيد، أو انسخه هناك والصقه هنا.",
  "s5.h": "ويرتدي مظهرك.",
  "s5.p": "ألوانك وأيقوناتك وخطوطك ومؤشرك، في تطبيقات GTK وQt وKDE.",
  "screens.h": "هكذا يبدو.",
  "screens.a1": "نافذة عبور الرئيسية: تطبيقات أحد الحواسيب، ولكلٍّ منها طريقة العرض التي يستخدمها",
  "screens.a2": "تحرير تطبيق: المضيف والأمر والأيقونة وإعداداته الخاصة",
  "features.h": "وكل ما تتوقعه أيضًا.",
  "features.g1": "الاتصال",
  "features.g2": "كل يوم",
  "f1.h": "متصفح التطبيقات", "f1.p": "شاهد تطبيقات الحاسوب المثبّتة بأيقوناتها، وأضفها بنقرة.",
  "f2.h": "تجهيز الحاسوب البعيد", "f2.p": "يجد الأدوات الناقصة على الحاسوب الآخر ويثبّتها بعد موافقتك.",
  "f3.h": "بدائل ذكية", "f3.p": "إن تعطّل تطبيق Wayland يعيد المحاولة دون مشاركة GPU، ثم عبر X11.",
  "f4.h": "إغلاق نظيف", "f4.p": "أغلق النافذة فينتهي أيضًا ما تركه التطبيق يعمل على المضيف.",
  "f5.h": "دخول سهل", "f5.p": "مفتاح SSH أولًا، وبعد كلمة مرور واحدة يمكنه إعداد الدخول بالمفتاح.",
  "f6.h": "Tailscale", "f6.p": "عنوان ثانٍ لكل حاسوب حين يتعذّر الوصول إلى المعتاد.",
  "f7.h": "قائمة تطبيقاتك", "f7.p": "ضع أي تطبيق بعيد في قائمة تطبيقات نظامك.",
  "f8.h": "نسخ احتياطي واستعادة", "f8.p": "كل شيء في ملف ‎.zip واحد، جاهز لحاسوب جديد.",
  "install.h": "احصل على عبور.",
  "install.p": "نزّل ملف نظامك من أحدث إصدار، ثم شغّل أمره. لا يحتاج الحاسوب الآخر إلا خادم SSH، ويعرض عبور تثبيت الباقي.",
  "install.cta": "أحدث إصدار",
  "install.appimage": "AppImage لأي توزيعة (افتحه بـ Gear Lever ليتحدّث)",
  "copy": "نسخ",
  "copied": "نُسخ",
  "copyfail": "حدّده وانسخه",
  "alpha.h": "عبور في مرحلة ألفا. ساعده ليعبر أبعد.",
  "alpha.p": "لم يُختبر حتى الآن إلا مع Ubuntu Server 26.04 حاسوبًا آخر وHyprland سطح مكتب. نحتاج إلى تجربتك ومساهمتك لإصلاح التطبيق.",
  "alpha.issue": "أبلغ عن مشكلة",
  "alpha.contrib": "كيف تساهم",
  "footer": "برنامج حر برخصة MIT.",
  "star": "ضع نجمة على GitHub",
};
const EN = { copied: "Copied", copyfail: "Select and copy" };
document.querySelectorAll("[data-i18n]").forEach(el => EN[el.dataset.i18n] = el.textContent);
document.querySelectorAll("[data-i18n-alt]").forEach(el => EN[el.dataset.i18nAlt] = el.alt);

let lang = "en";
const t = key => (lang === "ar" ? AR : EN)[key];

function setLang(l) {
  lang = l;
  const html = document.documentElement;
  html.lang = l;
  html.dir = l === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = t(el.dataset.i18n));
  document.querySelectorAll("[data-i18n-alt]").forEach(el => el.alt = t(el.dataset.i18nAlt));
  if (l === "ar") document.querySelectorAll("img[data-ar]").forEach(img => img.src = img.dataset.ar);
  document.title = l === "ar" ? "عبور" : "Obour";
  const button = document.getElementById("lang");
  button.textContent = l === "ar" ? "English" : "العربية"; // names the other language
  try { localStorage.setItem("lang", l); } catch {}
}

let saved = null;
try { saved = localStorage.getItem("lang"); } catch {}
const asked = new URLSearchParams(location.search).get("lang");
setLang(asked || saved || (navigator.language.startsWith("ar") ? "ar" : "en"));
// Switching reloads, so the text animations are built once, in the right language.
document.getElementById("lang").onclick = () => location.search = "?lang=" + (lang === "ar" ? "en" : "ar");

document.querySelectorAll(".cmds code").forEach(code => code.dataset.cmd = code.textContent);
document.querySelectorAll(".copy").forEach(button => button.onclick = async () => {
  const code = button.previousElementSibling;
  let ok = true;
  try { await navigator.clipboard.writeText(code.dataset.cmd); } catch { ok = false; getSelection().selectAllChildren(code); }
  button.textContent = t(ok ? "copied" : "copyfail");
  button.classList.toggle("done", ok);
  setTimeout(() => { button.textContent = t("copy"); button.classList.remove("done"); }, 1600);
});

// Motion. Without GSAP (blocked CDN) or with reduced motion, the page stays static.
if (window.gsap && window.ScrollTrigger && window.SplitText && window.TextPlugin) {
  gsap.registerPlugin(ScrollTrigger, SplitText, TextPlugin);
  const start = lang === "ar" ? "right" : "left", end = lang === "ar" ? "left" : "right";
  const once = trigger => ({ trigger, start: "top 82%", once: true });
  // Words, never letters: split into letters, Arabic stops joining up.
  const words = el => SplitText.create(el, { type: "words" }).words;
  const rise = { autoAlpha: 0, y: "0.6em", stagger: 0.04, duration: 0.6, ease: "power3.out" };

  // after the fonts, so words are measured in the real font
  document.fonts.ready.then(() => gsap.matchMedia().add("(prefers-reduced-motion: no-preference)", () => {
    document.documentElement.classList.add("motion");

    // 1. Hero: the intro, then three layers at three speeds
    gsap.timeline({ defaults: { ease: "power3.out" } })
      .from(".hero-logo", { autoAlpha: 0, scale: 0.85, rotation: -10, duration: 1 })
      .from(".badge", { autoAlpha: 0, y: 10, duration: 0.4 }, "<0.2")
      .from(lang === "ar" ? words(".hero h1") : SplitText.create(".hero h1", { type: "chars" }).chars,
            { autoAlpha: 0, yPercent: 60, stagger: 0.05, duration: 0.7 }, "<0.1")
      .from(words(".means"), rise, "<0.3")
      .from(words(".tagline"), { ...rise, stagger: 0.03 }, "<0.2")
      .from(".hero .links .button", { autoAlpha: 0, y: 16, stagger: 0.1, duration: 0.5 }, "<0.3");
    gsap.timeline({ scrollTrigger: { trigger: "#hero", start: "top top", end: "bottom top", scrub: true } })
      .to(".hero-stripes", { yPercent: 20, ease: "none" }, 0)
      .to(".hero-logo", { y: -160, rotation: -8, ease: "none" }, 0)
      .to(".hero-text", { y: -80, autoAlpha: 0, ease: "none" }, 0)
      .to(".hint", { autoAlpha: 0, ease: "none" }, 0);

    // Paragraphs rise in, word by word, as they come into view
    gsap.utils.toArray(".captions li:first-child > *, #install > p, #alpha > p")
      .forEach(el => gsap.from(words(el), { ...rise, scrollTrigger: once(el) }));

    // Section headings: a band of roadworks paint sweeps across and leaves the heading behind
    gsap.utils.toArray("#screens h2, #features h2, #install h2, #alpha h2").forEach(h => {
      const wipe = h.appendChild(document.createElement("span"));
      wipe.className = "wipe";
      const text = words(h);
      gsap.set(text, { autoAlpha: 0 });
      gsap.timeline({ scrollTrigger: once(h) })
        .fromTo(wipe, { scaleX: 0, transformOrigin: start }, { scaleX: 1, duration: 0.45, ease: "power3.inOut" })
        .set(text, { autoAlpha: 1 })
        .to(wipe, { scaleX: 0, transformOrigin: end, duration: 0.55, ease: "power3.out" });
    });

    // 2–3. The story: pinned while the app crosses, step by step.
    // Distances come from the layout, so they're right in both directions (LTR and RTL).
    // Each element's first fromTo draws its start state right away: no road, the app on the other computer.
    const left = sel => document.querySelector(sel).getBoundingClientRect().left;
    const toRemote = () => left(".remote .screen") - left(".local .screen");
    const captions = gsap.utils.toArray(".captions li");
    const story = gsap.timeline({
      defaults: { ease: "power2.inOut" },
      scrollTrigger: { trigger: "#story", start: "top top", end: "+=" + captions.length * 90 + "%", pin: true, scrub: 1, invalidateOnRefresh: true },
    });
    const caption = i => story
      .to(captions[i - 1], { autoAlpha: 0, y: -20, duration: 0.4 })
      .fromTo(captions[i], { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.01 }, "<0.3")
      .fromTo(words(captions[i].children), { autoAlpha: 0, y: "0.6em" }, { autoAlpha: 1, y: 0, stagger: 0.03, duration: 0.5, ease: "power3.out" }, "<");

    story.to({}, { duration: 0.5 });
    caption(1).fromTo(".road", { scaleX: 0 }, { scaleX: 1, duration: 1 }, "<");
    caption(2)
      .fromTo(".win", { x: toRemote }, { x: 0, duration: 1.4 }, "<")
      .to(".win", { y: -24, scale: 1.08, duration: 0.7, yoyo: true, repeat: 1, ease: "sine.inOut" }, "<")
      .fromTo(".road-mode", { color: "#B8B3A8" }, { color: "#F2B134", duration: 0.4, yoyo: true, repeat: 1 }, "<0.3");
    caption(3).fromTo(".waves .w", { autoAlpha: 0 }, { autoAlpha: 1, stagger: 0.2, duration: 0.3 }, "<");
    caption(4)
      .fromTo(".file", { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.2 }, "<")
      .fromTo(".file", { x: () => -toRemote() }, { x: 0, duration: 1.2 }, "<");
    caption(5).fromTo(".bar", { backgroundColor: "#B8B3A8" }, { backgroundColor: "#F2B134", duration: 0.6 }, "<")
      .to({}, { duration: 0.6 }); // hold the last frame a moment

    // 4. Screenshots drive in on the road from the reading start, tilted, and park flat
    const from = lang === "ar" ? 1 : -1;
    gsap.utils.toArray(".shots img").forEach((img, i) =>
      gsap.fromTo(img, { xPercent: from * (60 + i * 25), rotationY: from * -22, autoAlpha: 0 },
        { xPercent: 0, rotationY: 0, autoAlpha: 1, ease: "none",
          scrollTrigger: { trigger: ".shots", start: "top 95%", end: "center 55%", scrub: 1 } }));
    gsap.fromTo(".lane", { scaleX: 0 }, { scaleX: 1, ease: "none",
      scrollTrigger: { trigger: ".shots", start: "top 80%", end: "bottom 60%", scrub: true } });

    // 5. Features: each lane paints itself down as you scroll; icons draw, then their words arrive
    gsap.utils.toArray(".lane-v").forEach(lane => gsap.fromTo(lane, { scaleY: 0 }, { scaleY: 1, ease: "none",
      scrollTrigger: { trigger: lane.parentNode, start: "top 75%", end: "bottom 60%", scrub: true } }));
    const paths = gsap.utils.toArray(".icon path");
    paths.forEach(p => { const n = p.getTotalLength(); gsap.set(p, { strokeDasharray: n, strokeDashoffset: n }); });
    gsap.set(".group li > :not(svg)", { autoAlpha: 0, y: 12 });
    ScrollTrigger.batch(".group li", {
      start: "top 88%", once: true,
      onEnter: items => items.forEach((li, i) => gsap.timeline({ delay: i * 0.06 })
        .to(li.querySelector("path"), { strokeDashoffset: 0, duration: 0.9, ease: "power2.out" })
        .to(li.querySelectorAll(":scope > :not(svg)"), { autoAlpha: 1, y: 0, stagger: 0.06, duration: 0.5, ease: "power3.out" }, "<0.25")),
    });

    // 6. Install: the crossing is painted bar by bar, then each command types itself out
    gsap.from(".crossing i", { scaleX: 0, stagger: 0.07, duration: 0.6, ease: "power3.out", scrollTrigger: once(".crossing") });
    const codes = gsap.utils.toArray(".cmds code");
    gsap.set(codes, { text: "" });
    const typing = gsap.timeline({ scrollTrigger: once(".cmds") });
    codes.forEach(code => typing
      .add(() => code.classList.add("typing"))
      .to(code, { text: code.dataset.cmd, duration: code.dataset.cmd.length / 38, ease: "none" })
      .add(() => code.classList.remove("typing")));

    // 7. Roadworks: the barrier swings in
    gsap.from(".barrier", { scaleX: 0, duration: 0.8, ease: "power3.out", scrollTrigger: once(".barrier") });

    // The end of the road: the stop line is painted, the name settles behind it
    gsap.timeline({ scrollTrigger: once("footer") })
      .from(".stop-line", { scaleX: 0, duration: 0.9, ease: "power3.out" })
      .from(".end > *", { yPercent: 40, autoAlpha: 0, stagger: 0.12, duration: 1.2, ease: "power3.out" }, "<0.2");

    return () => document.documentElement.classList.remove("motion");
  }));
}
