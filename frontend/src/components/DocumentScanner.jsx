import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, ImagePlus, Loader2, RotateCw, ScanLine, Trash2, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { CompactSelect } from '@/components/ui/Field'
import { jpegsToPdf } from '@/lib/pdf'

const MAX_SIDE = 2000 // px on the long edge: sharp enough to read, small enough to upload
const QUALITY = 0.85
const MAX_BYTES = 10 * 1024 * 1024
const CARD_KINDS = new Set(['photo_id', 'insurance_card_front', 'insurance_card_back'])
const ASPECT = { card: 85.6 / 53.98, page: 8.5 / 11 } // ID-1 card, US Letter portrait

function guideRect(vw, vh, aspect) {
  let w = vw * 0.9
  let h = w / aspect
  if (h > vh * 0.9) {
    h = vh * 0.9
    w = h * aspect
  }
  return { x: (vw - w) / 2, y: (vh - h) / 2, w, h }
}

/** Grayscale + auto-levels (2nd–98th percentile stretch): flat, high-contrast "scanned paper" look. */
function enhance(ctx, w, h) {
  const img = ctx.getImageData(0, 0, w, h)
  const d = img.data
  const hist = new Uint32Array(256)
  for (let i = 0; i < d.length; i += 4) {
    const y = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000
    d[i] = y
    hist[y | 0]++
  }
  const total = w * h
  let lo = 0
  let hi = 255
  for (let acc = 0; lo < 255 && (acc += hist[lo]) < total * 0.02; lo++);
  for (let acc = 0; hi > 0 && (acc += hist[hi]) < total * 0.02; hi--);
  const range = Math.max(1, hi - lo)
  for (let i = 0; i < d.length; i += 4) {
    const v = Math.max(0, Math.min(255, ((d[i] - lo) * 255) / range))
    d[i] = d[i + 1] = d[i + 2] = v
  }
  ctx.putImageData(img, 0, 0)
}

/** Draw a source (video/image) region onto a canvas, scaled to MAX_SIDE, optionally enhanced. */
function render(source, sx, sy, sw, sh, { doEnhance, rotate = 0 }) {
  const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh))
  const w = Math.round(sw * scale)
  const h = Math.round(sh * scale)
  const turned = rotate % 180 !== 0
  const canvas = document.createElement('canvas')
  canvas.width = turned ? h : w
  canvas.height = turned ? w : h
  const ctx = canvas.getContext('2d', { willReadFrequently: doEnhance })
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.translate(canvas.width / 2, canvas.height / 2)
  ctx.rotate((rotate * Math.PI) / 180)
  ctx.drawImage(source, sx, sy, sw, sh, -w / 2, -h / 2, w, h)
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  if (doEnhance) enhance(ctx, canvas.width, canvas.height)
  return canvas
}

async function toPage(canvas) {
  const blob = await new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error('Could not encode the image'))), 'image/jpeg', QUALITY))
  return { blob, url: URL.createObjectURL(blob), width: canvas.width, height: canvas.height }
}

function loadImage(url) {
  return new Promise((res, rej) => {
    const img = new Image()
    img.onload = () => res(img)
    img.onerror = () => rej(new Error('That file is not an image the browser can read (try JPEG or PNG).'))
    img.src = url
  })
}

function cameraError(err) {
  const name = err?.name || ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'Camera access was blocked. Allow the camera for this site, or use “Choose photos” below.'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No camera found on this device. Use “Choose photos” to add pictures or files from your scanner.'
  if (name === 'NotReadableError') return 'The camera is in use by another app. Close it and try again.'
  return `Could not start the camera${err?.message ? `: ${err.message}` : ''}.`
}

/**
 * Scan a document with the device camera (webcam, phone, document camera) or from photos/scanner images:
 * guide frame + crop, optional document enhancement, rotate, multi-page. One page saves as JPEG, several as one PDF.
 */
export function DocumentScanner({ kind, kindLabel, onClose, onSave }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const fileRef = useRef(null)
  const [devices, setDevices] = useState([])
  const [deviceId, setDeviceId] = useState('')
  const [ready, setReady] = useState(false)
  const [aspectVideo, setAspectVideo] = useState(4 / 3)
  const [cameraIssue, setCameraIssue] = useState(null)
  const [pages, setPages] = useState([])
  const [docMode, setDocMode] = useState(!CARD_KINDS.has(kind))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(false)
  const target = CARD_KINDS.has(kind) ? ASPECT.card : ASPECT.page

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setReady(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    async function start() {
      setCameraIssue(null)
      if (!navigator.mediaDevices?.getUserMedia) {
        setCameraIssue(window.isSecureContext ? 'This browser cannot use the camera.' : 'The camera only works over HTTPS or on localhost. Use “Choose photos” instead.')
        return
      }
      try {
        stop()
        const stream = await navigator.mediaDevices.getUserMedia({
          video: deviceId
            ? { deviceId: { exact: deviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } }
            : { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        })
        if (cancelled) return stream.getTracks().forEach((t) => t.stop())
        streamRef.current = stream
        const v = videoRef.current
        if (v) {
          v.srcObject = stream
          await v.play().catch(() => {})
        }
        // device labels are only available after permission is granted
        const all = await navigator.mediaDevices.enumerateDevices().catch(() => [])
        if (!cancelled) setDevices(all.filter((d) => d.kind === 'videoinput'))
      } catch (err) {
        if (!cancelled) setCameraIssue(cameraError(err))
      }
    }
    start()
    return () => {
      cancelled = true
      stop()
    }
  }, [deviceId, stop])

  // release every preview URL when the dialog goes away
  const pagesRef = useRef(pages)
  pagesRef.current = pages
  useEffect(() => () => pagesRef.current.forEach((p) => URL.revokeObjectURL(p.url)), [])

  const onMeta = () => {
    const v = videoRef.current
    if (v?.videoWidth) {
      setAspectVideo(v.videoWidth / v.videoHeight)
      setReady(true)
    }
  }

  const run = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (err) {
      setError(err?.message || 'Something went wrong while scanning.')
    } finally {
      setBusy(false)
    }
  }

  const capture = () =>
    run(async () => {
      const v = videoRef.current
      if (!v?.videoWidth) throw new Error('The camera is not ready yet.')
      const g = guideRect(v.videoWidth, v.videoHeight, target)
      const page = await toPage(render(v, g.x, g.y, g.w, g.h, { doEnhance: docMode }))
      setPages((p) => [...p, { ...page, source: null, crop: g, enhanced: docMode }])
      setFlash(true)
      setTimeout(() => setFlash(false), 150)
    })

  const addFiles = (files) =>
    run(async () => {
      const added = []
      for (const f of files) {
        if (!f.type.startsWith('image/')) throw new Error(`${f.name}: only images can be scanned here. Upload PDFs with the Upload button.`)
        const src = URL.createObjectURL(f)
        try {
          const img = await loadImage(src)
          added.push(await toPage(render(img, 0, 0, img.naturalWidth, img.naturalHeight, { doEnhance: docMode })))
        } finally {
          URL.revokeObjectURL(src)
        }
      }
      setPages((p) => [...p, ...added])
    })

  const rotate = (i) =>
    run(async () => {
      const img = await loadImage(pages[i].url)
      const page = await toPage(render(img, 0, 0, img.naturalWidth, img.naturalHeight, { doEnhance: false, rotate: 90 }))
      setPages((p) => p.map((x, j) => (j === i ? (URL.revokeObjectURL(x.url), { ...x, ...page }) : x)))
    })

  const remove = (i) => setPages((p) => p.filter((x, j) => (j === i ? (URL.revokeObjectURL(x.url), false) : true)))

  const save = () =>
    run(async () => {
      if (!pages.length) throw new Error('Scan at least one page first.')
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')
      let file
      if (pages.length === 1) {
        file = new File([pages[0].blob], `scan-${kind}-${stamp}.jpg`, { type: 'image/jpeg' })
      } else {
        const parts = await Promise.all(pages.map(async (p) => ({ jpeg: new Uint8Array(await p.blob.arrayBuffer()), width: p.width, height: p.height })))
        file = new File([jpegsToPdf(parts)], `scan-${kind}-${stamp}.pdf`, { type: 'application/pdf' })
      }
      if (file.size > MAX_BYTES) throw new Error(`The scan is ${(file.size / 1048576).toFixed(1)} MB; the limit is 10 MB. Remove a page or save in two parts.`)
      await onSave(file)
      stop()
    })

  const g = guideRect(100, 100 / aspectVideo, target) // guide as % of the preview
  const pct = (n, of) => `${(n / of) * 100}%`

  return (
    <Dialog title="Scan document" subtitle={`${kindLabel} · ${pages.length} page${pages.length === 1 ? '' : 's'} scanned`} onClose={() => { stop(); onClose() }} size="xl"
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <span className="font-body text-xs text-muted">
            {pages.length > 1 ? 'Saved as one PDF.' : pages.length === 1 ? 'Saved as a JPEG image.' : 'Line the document up with the frame, then capture.'}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => { stop(); onClose() }} disabled={busy}>Cancel</Button>
            <Button onClick={save} disabled={busy || !pages.length} className="gap-2">
              {busy ? <Loader2 size={15} className="animate-spin" /> : <ScanLine size={15} />} Save {pages.length > 1 ? `${pages.length} pages` : 'scan'}
            </Button>
          </div>
        </div>
      }>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_260px]">
        <div className="flex flex-col gap-3">
          {cameraIssue ? (
            <div className="flex min-h-56 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-hairline-strong bg-surface-subtle px-6 py-10 text-center">
              <Camera size={26} className="text-muted" />
              <p className="max-w-md font-body text-sm text-body">{cameraIssue}</p>
            </div>
          ) : (
            <div className="relative w-full overflow-hidden rounded-lg bg-black" style={{ aspectRatio: aspectVideo }}>
              <video ref={videoRef} onLoadedMetadata={onMeta} playsInline muted className="absolute inset-0 size-full object-cover" />
              {ready && (
                <div className="pointer-events-none absolute rounded-md border-2 border-white/90 shadow-[0_0_0_9999px_rgba(0,0,0,0.45)]"
                  style={{ left: pct(g.x, 100), top: pct(g.y, 100 / aspectVideo), width: pct(g.w, 100), height: pct(g.h, 100 / aspectVideo) }} />
              )}
              {flash && <div className="absolute inset-0 bg-white/70" />}
              {!ready && <div className="absolute inset-0 flex items-center justify-center text-white/80"><Loader2 className="animate-spin" /></div>}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={capture} disabled={busy || !ready || !!cameraIssue} className="gap-2"><Camera size={15} /> Capture page</Button>
            <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={busy} className="gap-2"><ImagePlus size={15} /> Choose photos</Button>
            <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple hidden
              onChange={(e) => { const f = [...(e.target.files || [])]; e.target.value = ''; if (f.length) addFiles(f) }} />
            <label className="flex cursor-pointer items-center gap-2 font-body text-xs text-ink">
              <input type="checkbox" checked={docMode} onChange={(e) => setDocMode(e.target.checked)} className="accent-[var(--color-primary)]" />
              <Wand2 size={13} className="text-muted" /> Document mode (black &amp; white, high contrast)
            </label>
            {devices.length > 1 && (
              <CompactSelect aria-label="Camera" value={deviceId} onChange={(e) => setDeviceId(e.target.value)} className="ml-auto max-w-56 text-xs">
                <option value="">Default camera</option>
                {devices.map((d, i) => <option key={d.deviceId} value={d.deviceId}>{d.label || `Camera ${i + 1}`}</option>)}
              </CompactSelect>
            )}
          </div>
          <p className="font-body text-[11px] text-muted">
            Works with a webcam, a phone or tablet camera, or a document camera. From a flatbed scanner, scan to a file and use “Choose photos” (images) or Upload (PDF).
          </p>
          {error && <p role="alert" className="font-body text-xs text-error">{error}</p>}
        </div>

        <div className="flex flex-col gap-2">
          <p className="font-body text-xs font-semibold text-ink">Pages</p>
          {!pages.length && <p className="font-body text-xs text-muted">No pages yet.</p>}
          <ol className="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
            {pages.map((p, i) => (
              <li key={p.url} className="flex items-center gap-2.5 rounded-lg border border-hairline p-2">
                <img src={p.url} alt={`Page ${i + 1}`} className="h-16 w-16 rounded border border-hairline object-contain bg-white" />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="font-body text-xs font-medium text-ink">Page {i + 1}</span>
                  <span className="font-body text-[11px] text-muted">{p.width}×{p.height} · {Math.max(1, Math.round(p.blob.size / 1024))} KB</span>
                </div>
                <button aria-label={`Rotate page ${i + 1}`} disabled={busy} onClick={() => rotate(i)} className="cursor-pointer p-1 text-muted hover:text-ink"><RotateCw size={15} /></button>
                <button aria-label={`Remove page ${i + 1}`} disabled={busy} onClick={() => remove(i)} className="cursor-pointer p-1 text-muted hover:text-error"><Trash2 size={15} /></button>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Dialog>
  )
}
