/**
 * RichTextEditor — thin React 18-compatible wrapper around Quill 2.x.
 *
 * Do NOT use react-quill: it targets Quill 1.x and is not maintained for
 * React 18.  This component manually manages the Quill lifecycle with
 * useRef + useEffect so it behaves safely in strict-mode double-invocation.
 *
 * Props:
 *   value      {string}  — HTML string; used as initial content (fallback)
 *   delta      {object}  — Quill Delta object; preferred initial content (lossless)
 *   onChange   {fn}      — called with { html: string, delta: object } on every change
 *   readOnly   {bool}    — disable editing
 *   className  {string}  — added to the outer wrapper
 *
 * Imperative handle (via ref):
 *   insertVariable(token)  — inserts token at current selection (or end)
 */

import 'quill/dist/quill.snow.css';
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react';
import { cn } from '@/lib/utils.js';

const TOOLBAR_OPTIONS = [
  [{ header: [1, 2, 3, false] }],
  ['bold', 'italic', 'underline'],
  [{ color: [] }, { background: [] }],
  [{ list: 'ordered' }, { list: 'bullet' }],
  [{ align: [] }],
  ['link'],
  ['clean'],
];

export const RichTextEditor = forwardRef(function RichTextEditor(
  { value = '', delta = null, onChange, readOnly = false, className },
  ref,
) {
  const containerRef = useRef(null);
  const quillRef = useRef(null);
  // Track whether an external value push is in progress so we don't
  // fire onChange → parent state update → re-render → re-push loop.
  const suppressRef = useRef(false);

  // Expose insertVariable to parent via ref.
  useImperativeHandle(ref, () => ({
    insertVariable(token) {
      const quill = quillRef.current;
      if (!quill) return;
      const range = quill.getSelection(true);
      const index = range ? range.index : quill.getLength();
      quill.insertText(index, token, 'user');
      quill.setSelection(index + token.length, 0, 'silent');
    },
  }));

  // Instantiate Quill exactly once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || quillRef.current) return;

    // Quill 2.x ships as an ES module — dynamic import keeps the CSS side-effect
    // isolated to this component's lifecycle.
    let destroyed = false;
    import('quill').then(({ default: Quill }) => {
      if (destroyed) return;

      const quill = new Quill(el, {
        theme: 'snow',
        readOnly,
        modules: {
          toolbar: TOOLBAR_OPTIONS,
        },
      });
      quillRef.current = quill;

      // Seed initial content — prefer delta (lossless) over html.
      suppressRef.current = true;
      if (delta && delta.ops) {
        quill.setContents(delta, 'silent');
      } else if (value) {
        quill.clipboard.dangerouslyPasteHTML(value);
      }
      suppressRef.current = false;

      // Wire up the change emitter.
      quill.on('text-change', () => {
        if (suppressRef.current) return;
        onChange?.({
          html: quill.root.innerHTML,
          delta: quill.getContents(),
        });
      });
    });

    return () => {
      destroyed = true;
      // Quill 2.x does not have a public destroy(); remove the toolbar node
      // that Quill inserts above the container so strict-mode re-mount is clean.
      if (quillRef.current) {
        const toolbar = el.previousSibling;
        if (toolbar && toolbar.classList?.contains('ql-toolbar')) {
          toolbar.remove();
        }
        quillRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync readOnly prop changes.
  useEffect(() => {
    quillRef.current?.enable(!readOnly);
  }, [readOnly]);

  // Sync external value changes — only when the editor is NOT focused and
  // the incoming content actually differs from what is currently rendered.
  useEffect(() => {
    const quill = quillRef.current;
    if (!quill) return;
    if (quill.hasFocus()) return;

    suppressRef.current = true;
    if (delta && delta.ops) {
      const current = JSON.stringify(quill.getContents());
      const incoming = JSON.stringify(delta);
      if (current !== incoming) {
        quill.setContents(delta, 'silent');
      }
    } else {
      const currentHtml = quill.root.innerHTML;
      if (value !== undefined && currentHtml !== value) {
        quill.clipboard.dangerouslyPasteHTML(value ?? '');
      }
    }
    suppressRef.current = false;
  }, [value, delta]);

  return (
    <div className={cn('rte-wrapper', className)}>
      {/*
       * Quill mounts its toolbar BEFORE this div and the editor INTO this div.
       * The outer wrapper lets us apply sizing without fighting Quill's own DOM.
       */}
      <div ref={containerRef} />
    </div>
  );
});
