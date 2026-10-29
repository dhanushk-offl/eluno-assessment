import QRCode from 'qrcode';

// Encode only an internal reference, not customer information or a fake verification URL.
export function invoiceReference(operation: any) {
  return `Eluno invoice: ${operation.data.document_no}\nStore: ${operation.data.snapshot.store.code}\nOrder ID: ${operation.data.order.id}`;
}

export function invoiceQr(operation: any) {
  return QRCode.toDataURL(invoiceReference(operation), { margin: 2, width: 220, errorCorrectionLevel: 'M' });
}

const amount = (paise: number) => `Rs. ${(paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export async function downloadInvoice(operation: any, store: any) {
  const [{ jsPDF }, qr, logoResponse] = await Promise.all([
    import('jspdf'), invoiceQr(operation), fetch('/eluno-wordmark.png')
  ]);
  if (!logoResponse.ok) throw new Error('Unable to load the Eluno logo. Please try again.');
  const logo = new Uint8Array(await logoResponse.arrayBuffer());
  const { snapshot, document_no: invoice, order, charge } = operation.data;
  const doc = new jsPDF({ unit: 'mm', format: 'a4' });
  const width = doc.internal.pageSize.getWidth();
  const bottom = doc.internal.pageSize.getHeight() - 18;
  let y = 20;
  const line = () => { doc.setDrawColor(220, 220, 216); doc.line(18, y, width - 18, y); y += 7; };
  const ensure = (height: number) => { if (y + height > bottom) { doc.addPage(); y = 20; } };
  const text = (value: string, x: number, size = 10, style: 'normal' | 'bold' = 'normal') => {
    doc.setFont('helvetica', style); doc.setFontSize(size); doc.text(value, x, y);
  };
  const right = (value: string, size = 10, style: 'normal' | 'bold' = 'normal') => {
    doc.setFont('helvetica', style); doc.setFontSize(size); doc.text(value, width - 18, y, { align: 'right' });
  };
  doc.addImage(logo, 'PNG', 18, y - 4, 29, 0);
  text('SALE INVOICE', 135, 12, 'bold'); y += 16;
  text(store.name || `Eluno ${snapshot.store.code}`, 18, 12, 'bold'); y += 6;
  text([store.address1, store.province_code, store.zip, store.country_code].filter(Boolean).join(', '), 18, 9); y += 10;
  line();
  text(`Invoice: ${invoice}`, 18, 10, 'bold'); right(`Date: ${snapshot.billing_date}`); y += 7;
  text(`Customer: ${snapshot.customer.name}`, 18); right(`Store: ${snapshot.store.code}`); y += 6;
  text(`Phone: ${snapshot.customer.phone}`, 18); right(`Associate: ${snapshot.staff_id}`); y += 9;
  line();
  text('ITEM', 18, 9, 'bold'); right('TOTAL', 9, 'bold'); y += 7;
  for (const item of snapshot.lines) {
    const title = doc.splitTextToSize(item.variant.title, 110);
    ensure(title.length * 5 + 20);
    doc.setFont('helvetica', 'bold'); doc.setFontSize(10); doc.text(title, 18, y);
    right(amount(item.total_paise), 10, 'bold'); y += title.length * 5 + 1;
    text(`SKU: ${item.variant.sku}   HSN: ${item.variant.hsn}   Qty: 1`, 18, 9); y += 5;
    text(`MRP: ${amount(item.mrp_paise)}   Discount: ${amount(item.discount_paise)}`, 18, 9); y += 5;
    text(`Taxable: ${amount(item.taxable_paise)}   CGST: ${amount(item.cgst_paise)}   SGST: ${amount(item.sgst_paise)}`, 18, 9); y += 9;
  }
  ensure(70); line();
  for (const [label, value] of [['Subtotal', snapshot.totals.mrp_paise], ['Discount', snapshot.totals.discount_paise], ['Taxable value', snapshot.totals.taxable_paise], ['CGST', snapshot.totals.cgst_paise], ['SGST', snapshot.totals.sgst_paise], ['Total paid', snapshot.totals.total_paise]] as [string, number][]) {
    text(label, 18, label === 'Total paid' ? 12 : 10, label === 'Total paid' ? 'bold' : 'normal');
    right(amount(value), label === 'Total paid' ? 12 : 10, label === 'Total paid' ? 'bold' : 'normal'); y += 6;
  }
  y += 4; ensure(80); line();
  text(`Payment: ${snapshot.method.toUpperCase()}`, 18, 10, 'bold'); y += 6;
  text(`Payment reference: ${charge?.charge_id ?? 'Not available'}`, 18, 9); y += 6;
  text(`Order number: #${order.number} (Order ID: ${order.id})`, 18, 9); y += 6;
  text('Prices include GST. QR contains invoice and order references.', 18, 9); y += 6;
  text('Demo document: GSTIN and full address are not in the supplied data.', 18, 8);
  y += 6;
  doc.addImage(qr, 'PNG', 18, y, 27, 27);
  doc.save(`${invoice.replace(/[^a-zA-Z0-9_-]/g, '-')}.pdf`);
}
