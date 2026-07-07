import { useEffect, useRef } from "react";

/**
 * A ref whose `.current` flips to false once the component unmounts. Guard
 * `setState` in pollers / async callbacks (`if (!mounted.current) return;`) so a
 * request that resolves after navigation doesn't update an unmounted component.
 */
export function useIsMounted() {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return mounted;
}

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal accessibility: close on Escape, focus the dialog (or its first focusable
 * control) on open, trap Tab focus inside it, and restore focus to the
 * previously-focused element on close. Attach the returned ref to the dialog
 * container (give it `tabIndex={-1}` and `role="dialog" aria-modal="true"`).
 *
 * `onClose` is read through a ref, so passing an inline callback is fine — the
 * effect runs once per open, not on every render.
 */
export function useModalDismiss<T extends HTMLElement = HTMLDivElement>(
  onClose: () => void
) {
  const ref = useRef<T>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === "Tab" && ref.current) {
        const focusables = Array.from(
          ref.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
        ).filter((el) => el.offsetParent !== null);
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener("keydown", onKey);

    const node = ref.current;
    if (node) {
      const firstFocusable = node.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (firstFocusable ?? node).focus();
    }

    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, []);

  return ref;
}
