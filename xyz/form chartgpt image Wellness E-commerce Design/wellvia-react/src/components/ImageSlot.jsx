import { useEffect, useRef, useState } from 'react';

/**
 * ImageSlot — a drop-in image placeholder.
 * Drag & drop an image onto it, or pass a `src` to hard-code one.
 * Dropped images persist in localStorage by `id` so they survive reloads.
 *
 * Replace these with your own <img> tags or a CMS image once you wire up real photos.
 */
export default function ImageSlot({
  id,
  src,
  alt = '',
  shape = 'rect', // rect | rounded | circle | pill
  radius = 16,
  placeholder = 'Drop image',
  className = '',
  style = {},
}) {
  const storageKey = id ? `wv-img-${id}` : null;
  const [img, setImg] = useState(src || null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (src || !storageKey) return;
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) setImg(saved);
    } catch {
      /* ignore */
    }
  }, [src, storageKey]);

  const handleFile = (file) => {
    if (!file || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      setImg(dataUrl);
      try {
        if (storageKey) localStorage.setItem(storageKey, dataUrl);
      } catch {
        /* storage may be full; image still shows this session */
      }
    };
    reader.readAsDataURL(file);
  };

  const radiusStyle =
    shape === 'circle'
      ? '50%'
      : shape === 'pill'
      ? '999px'
      : shape === 'rounded'
      ? `${radius}px`
      : '0px';

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFile(e.dataTransfer.files?.[0]);
      }}
      className={`relative overflow-hidden cursor-pointer select-none ${className}`}
      style={{ borderRadius: radiusStyle, ...style }}
    >
      {img ? (
        <img src={img} alt={alt} className="w-full h-full object-cover block" />
      ) : (
        <div
          className="w-full h-full flex items-center justify-center text-center px-3"
          style={{
            background:
              'linear-gradient(160deg,#efe9df,#e4dccd)',
          }}
        >
          <span className="text-[11px] tracking-wide text-muted/80">{placeholder}</span>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => handleFile(e.target.files?.[0])}
      />
    </div>
  );
}
