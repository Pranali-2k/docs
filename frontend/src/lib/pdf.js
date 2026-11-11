/**
 * Minimal PDF writer: one JPEG per page, fitted onto US Letter with a small margin.
 * Enough for scanned documents; no dependencies. Pages: [{ jpeg: Uint8Array, width, height }].
 */
const enc = new TextEncoder()

export function jpegsToPdf(pages) {
  if (!pages.length) throw new Error('Nothing to save: scan at least one page.')
  const chunks = []
  const offsets = []
  let length = 0
  const push = (part) => {
    const bytes = typeof part === 'string' ? enc.encode(part) : part
    chunks.push(bytes)
    length += bytes.length
  }
  const obj = (num, body) => {
    offsets[num] = length
    push(`${num} 0 obj\n`)
    for (const b of [].concat(body)) push(b)
    push('\nendobj\n')
  }

  push('%PDF-1.4\n')
  push(new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a])) // binary marker comment

  const W = 612
  const H = 792
  const M = 18
  const kids = pages.map((_, i) => `${3 + i * 3} 0 R`).join(' ')
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>')
  obj(2, `<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`)
  pages.forEach((p, i) => {
    const pageNo = 3 + i * 3
    const scale = Math.min((W - 2 * M) / p.width, (H - 2 * M) / p.height)
    const w = +(p.width * scale).toFixed(2)
    const h = +(p.height * scale).toFixed(2)
    const x = +((W - w) / 2).toFixed(2)
    const y = +((H - h) / 2).toFixed(2)
    const content = `q ${w} 0 0 ${h} ${x} ${y} cm /Im0 Do Q`
    obj(pageNo, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} ${H}] /Resources << /XObject << /Im0 ${pageNo + 2} 0 R >> >> /Contents ${pageNo + 1} 0 R >>`)
    obj(pageNo + 1, [`<< /Length ${enc.encode(content).length} >>\nstream\n`, content, '\nendstream'])
    obj(pageNo + 2, [
      `<< /Type /XObject /Subtype /Image /Width ${p.width} /Height ${p.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.jpeg.length} >>\nstream\n`,
      p.jpeg,
      '\nendstream',
    ])
  })

  const count = 3 + pages.length * 3
  const xref = length
  push(`xref\n0 ${count}\n0000000000 65535 f \n`)
  for (let n = 1; n < count; n++) push(`${String(offsets[n]).padStart(10, '0')} 00000 n \n`)
  push(`trailer\n<< /Size ${count} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`)
  return new Blob(chunks, { type: 'application/pdf' })
}
