// "Make the call": each [data-q] block holds answer buttons with data-say (the reply) and data-right on the right one.
// Picking one shows its reply; nothing is saved or scored.
export function wireQuiz(root: ParentNode) {
  root.querySelectorAll<HTMLElement>('[data-q]').forEach((q) => {
    const out = q.querySelector<HTMLElement>('.ans');
    q.querySelectorAll<HTMLButtonElement>('button[data-say]').forEach((b) => b.addEventListener('click', () => {
      q.querySelectorAll('button[data-say]').forEach((x) => x.classList.toggle('on', x === b));
      if (out) { out.hidden = false; out.textContent = b.dataset.say ?? ''; }
    }));
  });
}
