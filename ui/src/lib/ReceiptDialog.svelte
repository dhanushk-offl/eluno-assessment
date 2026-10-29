<script lang="ts">
  import X from 'phosphor-svelte/lib/X';
  import DownloadSimple from 'phosphor-svelte/lib/DownloadSimple';
  import Printer from 'phosphor-svelte/lib/Printer';
  export let operation: any;
  export let store: any;
  export let qr: string;
  export let downloading = false;
  export let error = '';
  export let onClose: () => void;
  export let onDownload: () => void;
  const money = (n: number) => new Intl.NumberFormat('en-IN', { style:'currency', currency:'INR', minimumFractionDigits:2 }).format((n || 0) / 100);
  function modal(node: HTMLDialogElement) { node.showModal(); return { destroy() { if (node.open) node.close(); } }; }
  $: snapshot = operation.data.snapshot;
</script>

<dialog class="receipt-dialog" use:modal on:close={onClose} aria-labelledby="receipt-title">
  <div class="receipt-toolbar">
    <div><span>COMPLETED SALE</span><h2 id="receipt-title">Receipt & invoice</h2></div>
    <button class="icon-button" aria-label="Close receipt" on:click={onClose}><X size={22}/></button>
  </div>
  <div class="receipt-paper">
    <header class="receipt-header">
      <div><img src="/eluno-wordmark.png" alt="Eluno"/><h3>{store.name || `Eluno ${snapshot.store.code}`}</h3><p>{[store.address1, store.province_code, store.zip, store.country_code].filter(Boolean).join(', ')}</p></div>
      <div class="receipt-heading"><span>SALE INVOICE</span><strong>{operation.data.document_no}</strong><small>{snapshot.billing_date}</small></div>
    </header>
    <div class="receipt-details">
      <div><span>Customer</span><strong>{snapshot.customer.name}</strong><small>{snapshot.customer.phone}</small></div>
      <div><span>Store / associate</span><strong>{snapshot.store.code} / {snapshot.staff_id}</strong><small>Order #{operation.data.order.number}</small></div>
    </div>
    <div class="receipt-items"><div class="receipt-items-head"><span>ITEMS</span><span>AMOUNT</span></div>
      {#each snapshot.lines as line}
        <div class="receipt-line"><div><strong>{line.variant.title}</strong><small>{line.variant.sku} · HSN {line.variant.hsn} · Qty 1</small><small>MRP {money(line.mrp_paise)} · Discount {money(line.discount_paise)}</small><small>Taxable {money(line.taxable_paise)} · CGST {money(line.cgst_paise)} · SGST {money(line.sgst_paise)}</small></div><strong>{money(line.total_paise)}</strong></div>
      {/each}
    </div>
    <div class="receipt-totals">
      <div><span>Subtotal</span><strong>{money(snapshot.totals.mrp_paise)}</strong></div>
      <div><span>Discount</span><strong>−{money(snapshot.totals.discount_paise)}</strong></div>
      <div><span>Taxable value</span><strong>{money(snapshot.totals.taxable_paise)}</strong></div>
      <div><span>CGST</span><strong>{money(snapshot.totals.cgst_paise)}</strong></div>
      <div><span>SGST</span><strong>{money(snapshot.totals.sgst_paise)}</strong></div>
      <div class="receipt-grand-total"><span>Total paid</span><strong>{money(snapshot.totals.total_paise)}</strong></div>
    </div>
    <div class="receipt-payment"><div><strong>Paid via {snapshot.method.toUpperCase()}</strong><p>Payment reference: {operation.data.charge?.charge_id ?? 'Not available'}</p><p>Order ID: {operation.data.order.id}</p><p>Prices include GST.</p></div><img src={qr} alt="QR code containing invoice, store and order references"/></div>
    <p class="receipt-disclaimer">Demo document: GSTIN and full address are not in the supplied data. QR code contains invoice and order references.</p>
  </div>
  {#if error}<p class="receipt-error" role="alert">{error}</p>{/if}
  <div class="receipt-actions"><button class="secondary" on:click={onClose}>Close</button><button class="secondary" on:click={() => window.print()}><Printer size={19}/> Print</button><button class="primary" disabled={downloading} on:click={onDownload}><DownloadSimple size={19}/>{downloading ? 'Preparing PDF…' : 'Download PDF'}</button></div>
</dialog>
