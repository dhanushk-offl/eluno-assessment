<script lang="ts">
  // @ts-nocheck
  import { onMount } from 'svelte';
  import House from 'phosphor-svelte/lib/House';
  import Plus from 'phosphor-svelte/lib/Plus';
  import Receipt from 'phosphor-svelte/lib/Receipt';
  import ChartBar from 'phosphor-svelte/lib/ChartBar';
  import Eyeglasses from 'phosphor-svelte/lib/Eyeglasses';
  import MagnifyingGlass from 'phosphor-svelte/lib/MagnifyingGlass';
  import ArrowRight from 'phosphor-svelte/lib/ArrowRight';
  import ArrowLeft from 'phosphor-svelte/lib/ArrowLeft';
  import CaretDown from 'phosphor-svelte/lib/CaretDown';
  import CheckCircle from 'phosphor-svelte/lib/CheckCircle';
  import WarningCircle from 'phosphor-svelte/lib/WarningCircle';
  import ClockCounterClockwise from 'phosphor-svelte/lib/ClockCounterClockwise';
  import User from 'phosphor-svelte/lib/User';
  import Storefront from 'phosphor-svelte/lib/Storefront';
  import CreditCard from 'phosphor-svelte/lib/CreditCard';
  import Trash from 'phosphor-svelte/lib/Trash';
  import X from 'phosphor-svelte/lib/X';
  import ArrowClockwise from 'phosphor-svelte/lib/ArrowClockwise';
  import Info from 'phosphor-svelte/lib/Info';
  import ShoppingBag from 'phosphor-svelte/lib/ShoppingBag';
  import DownloadSimple from 'phosphor-svelte/lib/DownloadSimple';
  import ReceiptDialog from '$lib/ReceiptDialog.svelte';
  import { invoiceQr, downloadInvoice } from '$lib/invoice';

  let screen = 'home', step = 'customer', stores = [], staff = [], catalog = [], availability = {}, pending = [];
  let store = '', staffId = '', phone = '', customer = null, prescription = null, warnings = [];
  let pairs = [{ frame_variant_id: null, lens_variant_id: null }], pairIndex = 0;
  let search = '', lensType = 'all', selectedIndex = 'all', coating = 'all';
  let quote = null, method = 'upi', channel = 'walk_in', operation = null, orders = [], report = null;
  const indianDay = (value) => {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone:'Asia/Kolkata', year:'numeric', month:'2-digit', day:'2-digit' }).formatToParts(new Date(value));
    const get = (name) => parts.find((part) => part.type === name)?.value;
    return `${get('year')}-${get('month')}-${get('day')}`;
  };
  let orderSearch = '', selectedOrder = null, reason = 'CHANGED_MIND', reportDate = indianDay(new Date());
  let busy = false, booting = true, reportLoading = false, error = '', notice = '', showSettings = false, showTax = false, refundConfirm = false, resetConfirm = false;
  let recovery = null;
  let showReceipt = false, invoiceBusy = false, receiptStore = null, receiptQr = '';
  let receiptOperation = null;
  const steps = ['customer', 'frame', 'lens', 'review', 'payment'];
  const stepTitles = { customer: 'Customer', frame: 'Frame', lens: 'Lens', review: 'Review', payment: 'Payment' };
  const money = (n) => new Intl.NumberFormat('en-IN', { style:'currency', currency:'INR', minimumFractionDigits: 2 }).format((n || 0) / 100);
  const variant = (id) => catalog.find((v) => v.id === id);
  const shortName = (v) => v?.title?.split(' / ')[0] || 'Select a frame';
  const labelDate = (d) => d ? new Date(d + 'T12:00:00').toLocaleDateString('en-IN', { day:'numeric', month:'short', year:'numeric' }) : '—';
  $: frames = catalog.filter((v) => v.kind === 'frame' && v.rx_able && `${v.title} ${v.sku}`.toLowerCase().includes(search.toLowerCase()));
  $: lenses = catalog.filter((v) => v.kind === 'lens' && (lensType === 'all' || v.lens_type === lensType) && (selectedIndex === 'all' || v.index === selectedIndex) && (coating === 'all' || v.coating === coating));
  $: indexes = [...new Set(catalog.filter((v) => v.kind === 'lens').map((v) => v.index))].sort();
  $: coatings = [...new Set(catalog.filter((v) => v.kind === 'lens').map((v) => v.coating))];
  $: filteredOrders = orders.filter((o) => `${o.number} ${o.metadata?.invoice_no || ''} ${o.id} ${o.created_at ? indianDay(o.created_at) : ''}`.toLowerCase().includes(orderSearch.toLowerCase()));
  $: storeName = stores.find((s) => s.code === store)?.code || store || 'Select store';
  $: currentStaff = staff.find((s) => s.id === staffId);

  async function api(path, options = {}) {
    let response;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60_000);
    try { response = await fetch('/api' + path, { ...options, signal: controller.signal, headers: { 'content-type': 'application/json' } }); }
    catch { throw new Error(options.method === 'POST' ? 'Connection interrupted. Check the operation status before retrying.' : 'Unable to connect. Check the POS service and try again.'); }
    finally { clearTimeout(timeout); }
    let data;
    try { data = await response.json(); } catch { throw new Error('The service returned an unexpected response.'); }
    if (!response.ok && !(response.status === 409 && data.state)) {
      const problem = new Error(data.message || data.error || 'Something went wrong. Please try again.');
      problem.status = response.status; problem.code = data.error; throw problem;
    }
    return data;
  }
  const post = (path, data) => api(path, { method:'POST', body: JSON.stringify(data) });
  function start() {
    if (booting || busy) return;
    if (!store || !staffId) { showSettings=true; error='Select a store and associate before starting a sale.'; return; }
    if (step !== 'status' && (phone.trim() || customer || pairs.some((pair) => pair.frame_variant_id || pair.lens_variant_id))) { resetConfirm=true; return; }
    beginSale();
  }
  function beginSale() {
    resetConfirm=false; screen='sale'; step='customer'; phone=''; customer=null; prescription=null; warnings=[];
    pairs=[{frame_variant_id:null,lens_variant_id:null}]; pairIndex=0; search=''; lensType='all'; selectedIndex='all'; coating='all';
    quote=null; method='upi'; channel='walk_in'; showTax=false; operation=null; error=''; notice='';
  }
  async function loadStore() {
    if (!store) return;
    error='';
    try {
      staff = await api(`/stores/${store}/staff`);
      if (!staff.some((s) => s.id === staffId)) staffId = staff[0]?.id || '';
      if (typeof localStorage !== 'undefined') { localStorage.setItem('eluno-store', store); localStorage.setItem('eluno-staff', staffId); }
      pending = await api(`/pending-operations?store_code=${encodeURIComponent(store)}`);
      availability = await api(`/stores/${store}/availability`);
    } catch (e) { error=e.message; }
  }
  async function initialize() {
    busy=true;
    try {
      [stores,catalog] = await Promise.all([api('/stores'),api('/catalog')]);
      store = localStorage.getItem('eluno-store') || stores.find((s) => s.code === 'HSR')?.code || stores[0]?.code || '';
      staffId = localStorage.getItem('eluno-staff') || '';
      const saved = sessionStorage.getItem('eluno-operation');
      if (saved) recovery=JSON.parse(saved);
      await loadStore();
      await loadReport();
    } catch(e) { error=e.message; } finally { busy=false; booting=false; }
  }
  onMount(initialize);
  async function lookup() {
    error=''; notice=''; busy=true;
    try {
      let normalized = phone.trim().replace(/[\s()-]/g,'');
      if (/^\d{10}$/.test(normalized)) normalized='+91'+normalized;
      if (!/^\+[1-9]\d{7,14}$/.test(normalized)) throw new Error('Enter a valid phone number, including the country code.');
      phone=normalized;
      const result=await api(`/customers/lookup?phone=${encodeURIComponent(phone)}`);
      customer=result.customer; prescription=result.prescription; warnings=result.warnings || [];
    } catch(e) { customer=null; prescription=null; error=e.message; } finally { busy=false; }
  }
  function pickFrame(v) { if ((availability[v.id] ?? 0) < 1) return; pairs[pairIndex].frame_variant_id=v.id; pairs=[...pairs]; search=''; step='lens'; }
  function pickLens(v) { pairs[pairIndex].lens_variant_id=v.id; pairs=[...pairs]; getQuote(); }
  function payload() { return {phone,store_code:store,staff_id:staffId,pairs:pairs.map((p)=>({...p})),channel,method,manual_discount:null}; }
  function acceptQuote(result) {
    quote=result; customer=result.customer;
    prescription=result.lines.find((line) => line.prescription)?.prescription || null;
    warnings=result.warnings || [];
  }
  function comparableQuote(q) {
    const { method: ignoredMethod, quote_fingerprint: ignoredFingerprint, ...details } = q;
    return JSON.stringify(details);
  }
  async function getQuote() {
    if (!pairs.every((p) => p.lens_variant_id && p.frame_variant_id)) return;
    error=''; busy=true;
    try { acceptQuote(await post('/quotes',payload())); step='review'; }
    catch(e) { quote=null; step='lens'; error=e.message; }
    finally { busy=false; }
  }
  function addPair() { pairs=[...pairs,{frame_variant_id:null,lens_variant_id:null}]; pairIndex=pairs.length-1; quote=null; step='frame'; }
  function removePair(i) { pairs=pairs.filter((_,idx)=>idx!==i); pairIndex=Math.max(0,Math.min(pairIndex,pairs.length-1)); if(pairs.length) getQuote(); else start(); }
  async function checkout() {
    if (!quote || busy) return;
    if (recovery) { error='Check the previous operation status before taking another payment.'; return; }
    error=''; busy=true;
    let latest;
    try { latest=await post('/quotes',payload()); }
    catch(e) { error=e.message; step='review'; busy=false; return; }
    if (comparableQuote(latest) !== comparableQuote(quote)) {
      acceptQuote(latest); error='The prescription or bill changed. Please review the updated details before payment.'; step='review'; busy=false; return;
    }
    acceptQuote(latest);
    const key='sale_'+crypto.randomUUID().replaceAll('-','');
    const body={...payload(),operation_key:key,expected_total_paise:latest.totals.total_paise,expected_quote_fingerprint:latest.quote_fingerprint};
    recovery={kind:'checkout',key,body}; sessionStorage.setItem('eluno-operation',JSON.stringify(recovery));
    try { operation=await post('/checkouts',body); step='status'; await refreshPending(); if(operation.state==='completed' || operation.state==='failed') { recovery=null;sessionStorage.removeItem('eluno-operation'); } }
    catch(e) {
      await handleWriteFailure(e, 'checkout');
    }
    finally { busy=false; }
  }
  async function handleWriteFailure(problem, kind) {
    // A 503 or connection failure can follow a successful side effect. Keep
    // the original key and request until the backend confirms its state.
    if (kind === 'checkout') step='status';
    error=problem.message;
    if (problem.status !== 422 || !recovery) return;
    try {
      operation=await api(`/operations/${encodeURIComponent(recovery.key)}`);
      if (operation.state === 'completed' || operation.state === 'failed') {
        recovery=null; sessionStorage.removeItem('eluno-operation');
      }
    } catch (lookupError) {
      if (lookupError.status === 404 && lookupError.code === 'not_found') {
        recovery=null; sessionStorage.removeItem('eluno-operation');
        if (kind === 'checkout') step='review';
      }
      // An unavailable status check leaves the original recovery intact.
    }
  }
  async function refreshPending() { if(store) { try { pending=await api(`/pending-operations?store_code=${encodeURIComponent(store)}`); } catch {} } }
  async function resume(key, kind = 'checkout') {
    error=''; busy=true;
    try {
      let op;
      try { op=await post(`/operations/${encodeURIComponent(key)}/resume`,{}); }
      catch(e) { if (recovery?.key === key && e.code === 'not_found') op=await post(recovery.kind==='refund'?'/refunds':'/checkouts',recovery.body); else throw e; }
      operation=op; screen=kind==='refund'?'orders':'sale'; step='status';
      if (kind === 'refund') {
        try { orders=await api(`/orders?store_code=${encodeURIComponent(store)}`); selectedOrder=orders.find((order) => order.id === op.data.order_id) || null; }
        catch (e) { error=e.message; }
      }
      if(op.state==='completed' || op.state==='failed') { if(recovery?.key===key) { recovery=null;sessionStorage.removeItem('eluno-operation'); } }
      await refreshPending();
    } catch(e) { error=e.message; }
    finally { busy=false; }
  }
  async function loadOrders() { screen='orders'; error=''; selectedOrder=null; busy=true; try { orders=await api(`/orders?store_code=${encodeURIComponent(store)}`); } catch(e) { error=e.message; } finally { busy=false; } }
  async function loadReport() {
    if (!store) return;
    const requestedStore=store, requestedDate=reportDate;
    report=null; reportLoading=true;
    try {
      const result=await api(`/reports/day-end?store_code=${encodeURIComponent(requestedStore)}&on=${requestedDate}`);
      if (requestedStore===store && requestedDate===reportDate) report=result;
    } catch(e) {
      if (requestedStore===store && requestedDate===reportDate && screen==='reports') error=e.message;
    } finally {
      if (requestedStore===store && requestedDate===reportDate) reportLoading=false;
    }
  }
  async function showReport() { screen='reports'; error='';busy=true;await loadReport();busy=false; }
  async function refund() {
    if(!selectedOrder || busy)return;
    if (recovery) { error='Check the previous operation status before starting a refund.'; return; }
    busy=true;error='';
    const key='refund_'+crypto.randomUUID().replaceAll('-','');
    const body={operation_key:key,order_id:selectedOrder.id,staff_id:staffId,reason_code:reason};
    recovery={kind:'refund',key,body};sessionStorage.setItem('eluno-operation',JSON.stringify(recovery));
    try { operation=await post('/refunds',body);refundConfirm=false; if(operation.state==='completed'||operation.state==='failed'){recovery=null;sessionStorage.removeItem('eluno-operation');} await refreshPending(); orders=await api(`/orders?store_code=${encodeURIComponent(store)}`); selectedOrder=orders.find((order) => order.id === selectedOrder.id) || selectedOrder; }
    catch(e) { await handleWriteFailure(e, 'refund'); refundConfirm=false; }
    finally {busy=false;}
  }
  async function prepareReceipt() {
    const completed = operation;
    if (!completed || completed.state !== 'completed' || !completed.data?.snapshot || !completed.data?.order) throw new Error('Receipt details are not available for this sale.');
    if (receiptOperation !== completed) {
      const [details, qr] = await Promise.all([api(`/stores/${encodeURIComponent(completed.data.snapshot.store.code)}/details`), invoiceQr(completed)]);
      receiptStore=details; receiptQr=qr; receiptOperation=completed;
    }
    return { completed, details: receiptStore };
  }
  async function viewReceipt() {
    if (invoiceBusy) return;
    error=''; invoiceBusy=true;
    try { await prepareReceipt(); showReceipt=true; }
    catch(e) { error=e.message || 'Could not load the receipt. Please try again.'; }
    finally { invoiceBusy=false; }
  }
  async function saveInvoice() {
    if (invoiceBusy) return;
    error=''; invoiceBusy=true;
    try { const {completed, details}=await prepareReceipt(); await downloadInvoice(completed, details); }
    catch(e) { error=e.message || 'Could not create the PDF. Please try again.'; }
    finally { invoiceBusy=false; }
  }
  function setStaff() { localStorage.setItem('eluno-staff',staffId); }
  function modal(node) { node.showModal(); return { destroy() { if (node.open) node.close(); } }; }
  function statusText(state) { return ({completed:'Completed',failed:'Unable to complete',payment_captured_action_required:'Payment captured · action required',refund_paid_action_required:'Refund paid · action required',order_submitting:'Checking your order',refund_submitting:'Checking your refund',charging:'Checking your payment',terminal_refund_submitting:'Checking your refund'})[state] || 'In progress'; }
</script>

<svelte:head><title>Eluno | Store POS</title><meta name="description" content="Eluno store point of sale" /></svelte:head>
<div class="app-shell">
  <aside class="sidebar">
    <div class="brand"><img src="/eluno-wordmark.png" alt="Eluno" /><span>STORE POS</span></div>
    <div class="sidebar-label">WORKSPACE</div>
    <nav aria-label="Main navigation">
      <button class:nav-active={screen==='home'} aria-current={screen==='home' ? 'page' : undefined} disabled={busy} on:click={() => {screen='home';error='';loadReport();}}><House size={21} weight={screen==='home'?'fill':'regular'}/> Overview</button>
      <button class:nav-active={screen==='sale'} aria-current={screen==='sale' ? 'page' : undefined} disabled={booting || busy} on:click={start}><ShoppingBag size={21} weight={screen==='sale'?'fill':'regular'}/> New sale</button>
      <button class:nav-active={screen==='orders'} aria-current={screen==='orders' ? 'page' : undefined} disabled={busy} on:click={loadOrders}><Receipt size={21} weight={screen==='orders'?'fill':'regular'}/> Orders & returns</button>
      <button class:nav-active={screen==='reports'} aria-current={screen==='reports' ? 'page' : undefined} disabled={busy} on:click={showReport}><ChartBar size={21} weight={screen==='reports'?'fill':'regular'}/> Day-end</button>
    </nav>
    <div class="sidebar-bottom"><div class="side-note"><span class="orange-dot"></span> Store workspace</div><strong class="sidebar-store">{storeName}</strong><span class="sidebar-associate">{currentStaff?.id ? `Associate ${currentStaff.id}` : 'Select an associate'}</span><p>Thoughtful service,<br/>beautifully simple.</p>{#if pending.length}<span class="sidebar-pending">{pending.length} {pending.length === 1 ? 'operation needs' : 'operations need'} attention</span>{:else}<span>ELUNO · RETAIL</span>{/if}</div>
  </aside>
  <main class="main">
    <header class="topbar"><div class="breadcrumb"><span>STORE WORKSPACE</span><CaretDown size={12}/><strong>{screen==='home'?'Overview':screen==='sale'?'New sale':screen==='orders'?'Orders & returns':'Day-end'}</strong></div><button class="store-switch" disabled={booting || busy} on:click={() => showSettings=true}><span class="store-icon"><Storefront size={17}/></span><span><strong>{storeName}</strong><small>{currentStaff?.id || 'Select associate'}</small></span><CaretDown size={14}/></button></header>
    {#if error}<div class="global-message error" role="alert"><WarningCircle size={20}/><span>{error}</span><button aria-label="Dismiss error" on:click={() => error=''}><X size={16}/></button></div>{/if}
    {#if notice}<div class="global-message success" role="status"><CheckCircle size={20}/>{notice}</div>{/if}
    {#if recovery && (!operation || operation.key !== recovery.key)}<div class="recovery-banner"><WarningCircle size={20}/><div><strong>An operation needs your attention</strong><span>Check its status before starting another payment.</span></div><button class="text-button" on:click={() => resume(recovery.key,recovery.kind)}>View status <ArrowRight size={16}/></button></div>{/if}
    {#if screen === 'home'}
      <div class="page home-page"><div class="page-intro"><div><div class="eyebrow"><span class="orange-dot"></span> YOUR STORE, AT A GLANCE</div><h1>Good to see you.</h1><p>Everything you need for a thoughtful in-store experience.</p></div><div class="date-chip"><span>STORE</span><strong>{storeName}</strong></div></div>
        <div class="home-grid"><section class="hero-panel"><div class="hero-copy"><span class="hero-kicker">READY WHEN YOU ARE</span><h2>Make their<br/><em>next pair</em> memorable.</h2><p>From finding the right frame to a seamless checkout, start a new customer journey here.</p><button class="primary light" disabled={booting} on:click={start}>Start a new sale <ArrowRight size={19}/></button></div><div class="hero-guide"><span class="hero-guide-label">AT THE COUNTER</span><h3>From hello to checkout</h3><ol><li><span>01</span><div><strong>Find the customer</strong><small>Look up their prescription</small></div></li><li><span>02</span><div><strong>Choose their pair</strong><small>Select a frame and lens</small></div></li><li><span>03</span><div><strong>Review &amp; pay</strong><small>Confirm the total together</small></div></li></ol></div></section>
          <section class="overview-panel"><div class="section-top"><div><span class="kicker">TODAY AT {storeName}</span><h3>A little overview</h3></div><button class="icon-button" aria-label="Refresh overview" on:click={loadReport}><ArrowClockwise size={18}/></button></div><div class="overview-stat"><span>Net collections</span><strong>{report ? money(report.net_collections_paise) : '—'}</strong><small>After refunds · today</small></div><div class="stat-row"><div><span>Bills today</span><strong>{report?.bill_count ?? '—'}</strong></div><div><span>Pending items</span><strong>{pending.length}</strong></div></div><button class="inline-link" on:click={showReport}>View day-end report <ArrowRight size={16}/></button></section></div>
        <div class="section-heading"><div><span class="kicker">STORE TASKS</span><h2>More from your store</h2></div></div>
        <div class="quick-grid"><button class="quick-card" on:click={loadOrders}><span class="quick-icon pale"><Receipt size={24}/></span><div><strong>Orders & returns</strong><small>Find a bill or process a full refund.</small></div><ArrowRight size={19}/></button><button class="quick-card" on:click={showReport}><span class="quick-icon pale"><ChartBar size={24}/></span><div><strong>Day-end report</strong><small>See sales, payments and reconciliation.</small></div><ArrowRight size={19}/></button></div>
        {#if pending.length}<section class="pending-list"><div class="section-top"><div><span class="kicker">NEEDS ATTENTION</span><h3>Pending operations</h3></div></div>{#each pending as item}<div class="pending-item"><WarningCircle size={20}/><div><strong>{statusText(item.state)}</strong><small>{item.data.document_no} · {item.kind}</small></div><button class="inline-link" disabled={busy} on:click={() => resume(item.key,item.kind)}>Review <ArrowRight size={16}/></button></div>{/each}</section>{/if}
      </div>
    {:else if screen === 'sale'}
      <div class="sale-wrap"><div class="sale-heading"><div><span class="kicker">A LITTLE MORE PERSONAL</span><h1>New sale</h1><p>Let’s find the right pair, together.</p></div><button class="secondary compact" disabled={busy} on:click={start}><Plus size={16}/> Start over</button></div>
        {#if step !== 'status'}<nav class="stepper" aria-label="Sale progress">{#each steps as st,i}<div class:current={step===st} class:done={steps.indexOf(step)>i} aria-current={step===st ? 'step' : undefined}><span>{#if steps.indexOf(step)>i}<CheckCircle size={17} weight="fill"/>{:else}{i+1}{/if}</span><strong>{stepTitles[st]}</strong></div>{#if i<4}<div class="step-line"></div>{/if}{/each}</nav>{/if}
        <div class="sale-glance"><span>{customer ? customer.name : 'No customer selected'}</span><a href="#sale-summary">{quote ? money(quote.totals.total_paise) : 'View sale summary'} <ArrowRight size={16}/></a></div>
        <div class="work-layout"><section class="work-panel">
          {#if step === 'customer'}<div class="panel-title"><span class="panel-number">01</span><div><span class="kicker">FIRST, THE PERSON</span><h2>Find your customer</h2><p>Look up their profile and latest valid prescription.</p></div></div><form class="lookup-form" on:submit|preventDefault={lookup}><label for="phone">Phone number</label><div class="input-row"><div class="input-icon"><MagnifyingGlass size={20}/><input id="phone" type="tel" inputmode="tel" placeholder="e.g. +91 90000 00005" bind:value={phone} disabled={busy} on:input={() => { customer=null; prescription=null; warnings=[]; quote=null; }} required /></div><button class="primary" disabled={busy}>{busy?'Looking up…':'Find customer'} <ArrowRight size={17}/></button></div><small>Enter a 10-digit Indian number or include the country code.</small><div class="demo-lookup">Using the demo data? <button type="button" class="text-button" disabled={busy} on:click={() => { phone='9000000005'; customer=null; prescription=null; warnings=[]; quote=null; error=''; }}>Use sample number 9000000005</button></div></form>
            {#if customer}<div class="customer-card"><div class="customer-top"><div class="avatar">{customer.name?.charAt(0) || 'C'}</div><div><h3>{customer.name}</h3><p>{customer.phone}</p></div><span class="verified"><CheckCircle size={15} weight="fill"/> Customer found</span></div><div class="tag-row">{#each customer.tags as tag}<span class="tag">{tag.replace('campaign:','').replaceAll('_',' ')}</span>{/each}</div><div class="rx-heading"><div><span class="kicker">PRESCRIPTION ON FILE</span><h4>{prescription ? prescription.id : 'No valid prescription'}</h4></div>{#if prescription}<span class="valid-tag">Valid until {labelDate(prescription.valid_until)}</span>{/if}</div>{#if prescription}<div class="rx-grid"><div class="rx-head"><span>Eye</span><span>SPH</span><span>CYL</span><span>AXIS</span><span>ADD</span></div>{#each ['od','os'] as eye}<div class="rx-line"><strong>{eye.toUpperCase()}</strong><span>{prescription[eye].sph ?? '—'}</span><span>{prescription[eye].cyl ?? '—'}</span><span>{prescription[eye].axis ?? '—'}</span><span>{prescription[eye].add ?? '—'}</span></div>{/each}</div>{:else}<p class="inline-warning">An eye test is needed before selecting a lens.</p>{/if}{#each warnings as warning}<div class="inline-warning"><Info size={16}/>{warning}</div>{/each}<button class="primary full" disabled={!prescription} on:click={() => {step='frame';error='';}}>Continue to frame <ArrowRight size={18}/></button></div>{:else}<div class="quiet-state"><div class="quiet-icon"><User size={27}/></div><strong>A good fit starts with knowing them.</strong><p>Search by phone to bring up their details and prescription.</p></div>{/if}
          {:else if step === 'frame'}<div class="panel-title"><span class="panel-number">02</span><div><span class="kicker">THE FOUNDATION</span><h2>Choose a frame</h2><p>Available prescription frames at {storeName} · pair {pairIndex+1}</p></div></div><div class="filter-bar"><div class="input-icon"><MagnifyingGlass size={19}/><input aria-label="Search frames" placeholder="Search by name or SKU" bind:value={search}/></div><span>{frames.length} frames</span></div><div class="product-grid">{#each frames as v}<button class="product-card" class:unavailable={(availability[v.id] ?? 0)<1} disabled={(availability[v.id] ?? 0)<1} on:click={() => pickFrame(v)}><div class="product-visual"><Eyeglasses size={77} weight="thin"/><span>{(availability[v.id] ?? 0)>0?'IN STOCK':'OUT OF STOCK'}</span></div><div class="product-info"><div><small>{v.sku}</small><strong>{shortName(v)}</strong><span>{v.title.split(' / ')[1]}</span></div><b>{money(v.price_paise)}</b></div></button>{:else}<div class="empty">No frames match your search.</div>{/each}</div><button class="back-link" on:click={() => {step='customer';search='';}}><ArrowLeft size={17}/> Back to customer</button>
          {:else if step === 'lens'}<div class="panel-title"><span class="panel-number">03</span><div><span class="kicker">MADE FOR THEIR EYES</span><h2>Choose a lens</h2><p>Each lens is checked against {prescription?.id} before continuing.</p></div></div><div class="selection-note"><Eyeglasses size={21}/><span>Selected frame <strong>{shortName(variant(pairs[pairIndex]?.frame_variant_id))}</strong></span><button on:click={() => step='frame'}>Change</button></div><div class="lens-filters"><label>Lens type<select bind:value={lensType}><option value="all">All types</option><option value="single_vision">Single vision</option><option value="bifocal">Bifocal</option><option value="progressive">Progressive</option></select></label><label>Index<select bind:value={selectedIndex}><option value="all">All indexes</option>{#each indexes as index}<option value={index}>{index}</option>{/each}</select></label><label>Coating<select bind:value={coating}><option value="all">All coatings</option>{#each coatings as item}<option value={item}>{item.replaceAll('_',' ')}</option>{/each}</select></label></div><div class="lens-list">{#each lenses as v}<button class="lens-option" disabled={busy} on:click={() => pickLens(v)}><span class="lens-symbol"><span></span></span><div><strong>{v.lens_type.replaceAll('_',' ')} · {v.index}</strong><small>{v.coating.replaceAll('_',' ')} · {v.sku}</small></div><b>{money(v.price_paise)}</b><ArrowRight size={18}/></button>{:else}<div class="empty">No lenses match these filters.</div>{/each}</div><button class="back-link" on:click={() => step='frame'}><ArrowLeft size={17}/> Back to frames</button>
          {:else if step === 'review' && quote}<div class="panel-title"><span class="panel-number">04</span><div><span class="kicker">ALMOST THERE</span><h2>Review the details</h2><p>Everything in one place before the customer says yes.</p></div></div><div class="review-customer"><span class="avatar small" aria-hidden="true">{customer?.name?.charAt(0)}</span><div><strong>{customer?.name}</strong><small>{customer?.phone} · Rx {prescription?.id}</small></div><CheckCircle size={20} weight="fill"/></div><div class="review-title"><div><span class="kicker">THEIR SELECTION</span><h3>{pairs.length} {pairs.length===1?'pair':'pairs'}</h3></div><button class="text-button" disabled={busy} on:click={addPair}><Plus size={16}/> Add another pair</button></div>{#each pairs as pair,i}<div class="review-pair"><div class="pair-heading"><strong>Pair {i+1}</strong><button aria-label="Remove pair" disabled={busy} on:click={() => removePair(i)}><Trash size={17}/></button></div>{#each [pair.frame_variant_id,pair.lens_variant_id] as id}<div class="review-item"><span class="item-icon"><Eyeglasses size={21}/></span><div><strong>{shortName(variant(id))}</strong><small>{variant(id)?.title.split(' / ')[1]} · {variant(id)?.sku}</small></div><b>{money(variant(id)?.price_paise)}</b></div>{/each}</div>{/each}{#if error}<button class="secondary full" disabled={busy} on:click={getQuote}>Refresh quote <ArrowClockwise size={16}/></button>{/if}<div class="offers-box"><div class="offers-heading"><span class="offer-star">✳</span><div><strong>Offers, taken care of</strong><small>The best eligible combination has been applied.</small></div><b>−{money(quote.totals.discount_paise)}</b></div>{#each quote.offers.filter((o) => o.applied) as offer}<div class="offer-line"><span>{offer.offer_id.replaceAll('_',' ')}</span><strong>−{money(offer.amount_paise)}</strong></div>{:else}<small>No eligible offers for this selection.</small>{/each}</div><label class="channel-label">Sales channel<select bind:value={channel} disabled={busy} on:change={(event) => { channel=event.currentTarget.value; getQuote(); }}><option value="walk_in">Walk-in</option><option value="whatsapp_booked">WhatsApp booked</option><option value="online_pickup">Online pickup</option></select></label><button class="primary full" disabled={busy} on:click={() => step='payment'}>Continue to payment <ArrowRight size={18}/></button><button class="back-link" disabled={busy} on:click={() => step='lens'}><ArrowLeft size={17}/> Back to lenses</button>
          {:else if step === 'payment' && quote}<div class="panel-title"><span class="panel-number">05</span><div><span class="kicker">THE FINAL STEP</span><h2>Take payment</h2><p>Confirm the amount with your customer before charging.</p></div></div><div class="pay-amount"><span>AMOUNT TO COLLECT</span><strong>{money(quote.totals.total_paise)}</strong><small>Inclusive of all taxes</small></div><h3 class="payment-heading">How would they like to pay?</h3><div class="payment-options">{#each [['upi','UPI','Scan or pay with UPI'],['card','Card','Credit or debit card'],['cash','Cash','Cash at the counter']] as item}<button class:selected={method===item[0]} aria-pressed={method===item[0]} disabled={busy} on:click={() => method=item[0]}><span class="pay-icon"><CreditCard size={22}/></span><div><strong>{item[1]}</strong><small>{item[2]}</small></div><span class="radio"><span></span></span></button>{/each}</div><div class="secure-note"><Info size={18}/><span>Confirm once. If the connection drops, check the operation status before trying again.</span></div><button class="primary full" disabled={busy} on:click={checkout}>{busy?'Processing payment…':`Charge ${money(quote.totals.total_paise)}`} <ArrowRight size={18}/></button><button class="back-link" disabled={busy} on:click={() => step='review'}><ArrowLeft size={17}/> Back to review</button>
          {:else if step === 'status'}<div class="status-page">{#if operation?.state === 'completed'}<span class="status-icon good"><CheckCircle size={44} weight="fill"/></span><span class="kicker">ALL DONE</span><h2>Sale completed.</h2><p>Payment has been captured and the order is recorded.</p><div class="receipt-box"><div><span>Invoice number</span><strong>{operation.data.document_no}</strong></div><div><span>Order number</span><strong>#{operation.data.order?.number}</strong></div><div><span>Amount paid</span><strong>{money(operation.data.snapshot?.totals?.total_paise)}</strong></div></div><div class="sale-complete-actions"><button class="primary full" disabled={invoiceBusy} on:click={viewReceipt}><Receipt size={19}/> View receipt</button><button class="secondary full" disabled={invoiceBusy} on:click={saveInvoice}><DownloadSimple size={19}/>{invoiceBusy ? 'Preparing…' : 'Download invoice PDF'}</button><button class="secondary full" disabled={invoiceBusy} on:click={start}><Plus size={19}/> Start another sale</button></div><button class="back-link" on:click={loadOrders}>View orders</button>{:else}<span class="status-icon alert"><ClockCounterClockwise size={40}/></span><span class="kicker">PAYMENT STATUS</span><h2>{operation ? statusText(operation.state) : 'Checking your payment'}</h2><p>{operation?.data?.error || error || 'Your operation is saved. Check its status before taking another payment.'}</p>{#if operation}<div class="receipt-box"><div><span>Reference</span><strong>{operation.data.document_no}</strong></div><div><span>Status</span><strong>{operation.state}</strong></div></div>{/if}{#if operation?.state !== 'failed' && (operation?.key || recovery?.key)}<button class="primary full" disabled={busy} on:click={() => resume(operation?.key || recovery.key, 'checkout')}>{busy?'Checking…':'Check status'} <ArrowClockwise size={17}/></button>{/if}{#if operation?.state === 'failed'}<button class="secondary full" on:click={start}>Start a fresh sale</button>{/if}{/if}</div>{/if}
        </section>
        <aside class="bill-panel" id="sale-summary" aria-label="Current sale summary"><div class="bill-heading"><div><span class="kicker">THE DETAILS</span><h3>Current sale</h3></div><span class="bill-badge">{pairs.length} {pairs.length===1?'PAIR':'PAIRS'}</span></div>{#if customer}<div class="bill-customer"><span class="avatar tiny">{customer.name?.charAt(0)}</span><div><strong>{customer.name}</strong><small>{customer.phone}</small></div><CheckCircle size={16} weight="fill"/></div>{:else}<div class="bill-placeholder"><User size={20}/><span>Customer not selected yet</span></div>{/if}<div class="bill-items">{#each pairs as pair,i}<div class="bill-pair"><div class="bill-pair-title">PAIR {i+1}</div><div><span>Frame</span><strong>{pair.frame_variant_id?shortName(variant(pair.frame_variant_id)):'Not selected'}</strong></div><div><span>Lens</span><strong>{pair.lens_variant_id?shortName(variant(pair.lens_variant_id)):'Not selected'}</strong></div></div>{/each}</div><div class="bill-totals"><div><span>Subtotal</span><strong>{quote?money(quote.totals.mrp_paise):'—'}</strong></div><div class="discount"><span>Offers & savings</span><strong>{quote?'−'+money(quote.totals.discount_paise):'—'}</strong></div><button class="tax-toggle" aria-expanded={showTax} on:click={() => showTax=!showTax}><span>Tax breakdown</span><CaretDown size={15}/></button>{#if showTax && quote}<div><span>Taxable value</span><strong>{money(quote.totals.taxable_paise)}</strong></div><div><span>CGST</span><strong>{money(quote.totals.cgst_paise)}</strong></div><div><span>SGST</span><strong>{money(quote.totals.sgst_paise)}</strong></div>{/if}<div class="bill-total"><span>Total due<small>Includes all taxes</small></span><strong>{quote?money(quote.totals.total_paise):'—'}</strong></div></div><div class="bill-footer"><span>✳</span> Thoughtfully chosen, just for them.</div></aside></div></div>
    {:else if screen === 'orders'}<div class="page"><div class="page-intro"><div><span class="kicker">STORE RECORDS</span><h1>Orders & returns</h1><p>Find a bill, review its details or process a full refund.</p></div><button class="secondary compact" on:click={loadOrders}><ArrowClockwise size={17}/> Refresh</button></div><div class="orders-layout"><section class="white-panel"><div class="section-top"><div><span class="kicker">{storeName} · ALL ORDERS</span><h3>Recent orders</h3></div><span class="muted">{filteredOrders.length} orders</span></div><div class="input-icon order-search"><MagnifyingGlass size={19}/><input placeholder="Search invoice, order or date (YYYY-MM-DD)" aria-label="Search orders" bind:value={orderSearch}/></div><div class="order-list">{#each filteredOrders as order}<button class="order-row" class:selected={selectedOrder?.id===order.id} on:click={() => {selectedOrder=order;operation=null;error='';}}><span class="order-icon"><Receipt size={21}/></span><div><strong>{order.metadata?.invoice_no || `Order #${order.number}`}</strong><small>#{order.number} · {new Date(order.created_at).toLocaleDateString('en-IN', {timeZone:'Asia/Kolkata'})}</small></div><span class="order-meta"><strong>{money(order.total_paise)}</strong><small>{order.refunds?.length ? 'Refunded' : 'Paid'}</small></span><ArrowRight size={17}/></button>{:else}<div class="empty">No orders found for this store.</div>{/each}</div></section><section class="white-panel detail-panel">{#if selectedOrder}<span class="kicker">ORDER DETAILS</span><h2>{selectedOrder.metadata?.invoice_no || `Order #${selectedOrder.number}`}</h2><p class="muted">Shopify #{selectedOrder.number} · {new Date(selectedOrder.created_at).toLocaleDateString('en-IN', {timeZone:'Asia/Kolkata'})}</p><div class="detail-list"><div><span>Amount paid</span><strong>{money(selectedOrder.total_paise)}</strong></div><div><span>Payment</span><strong>{selectedOrder.metadata?.payment_method || '—'}</strong></div><div><span>Status</span><strong>{selectedOrder.refunds?.length?'Refunded':'Paid'}</strong></div></div><h3>Items</h3>{#each selectedOrder.lines as line}<div class="detail-item"><div><strong>{line.kind || 'Item'}</strong><small>Variant {line.variant_id}</small></div><strong>{money(line.total_paise)}</strong></div>{/each}{#if operation}<div class="operation-note" role="status"><strong>{statusText(operation.state)}</strong><p>{operation.data.error || (operation.state==='completed' ? `Credit note ${operation.data.document_no}` : 'Check the status before retrying.')}</p>{#if operation.state !== 'completed' && operation.state !== 'failed'}<button class="text-button" on:click={() => resume(operation.key,'refund')}>Check status <ArrowClockwise size={15}/></button>{/if}</div>{/if}{#if !selectedOrder.refunds?.length}<label class="channel-label">Reason for refund<select bind:value={reason}><option value="CHANGED_MIND">Changed mind</option><option value="RETURN">Return</option><option value="OTHER">Other</option></select></label><button class="secondary full" on:click={() => refundConfirm=true}>Process full refund <ArrowRight size={17}/></button>{/if}{:else if operation}<div class="empty-detail"><span class="quiet-icon"><ClockCounterClockwise size={30}/></span><h3>{statusText(operation.state)}</h3><p>{operation.data.error || `Reference ${operation.data.document_no}`}</p>{#if operation.state !== 'completed' && operation.state !== 'failed'}<button class="text-button" on:click={() => resume(operation.key,'refund')}>Check status <ArrowClockwise size={15}/></button>{/if}</div>{:else}<div class="empty-detail"><span class="quiet-icon"><Receipt size={30}/></span><h3>Select an order</h3><p>Choose a bill to view its details and refund options.</p></div>{/if}</section></div></div>
    {:else if screen === 'reports'}<div class="page"><div class="page-intro"><div><span class="kicker">YOUR STORE IN NUMBERS</span><h1>Day-end report</h1><p>A clear view of the day, reconciled against Shopify.</p></div><label class="date-input"><span>REPORT DATE</span><input type="date" bind:value={reportDate} on:change={loadReport}/></label></div>{#if report}<div class="report-grid"><section class="report-feature"><span class="kicker">NET COLLECTIONS · {storeName}</span><h2>{money(report.net_collections_paise)}</h2><p>Sales received, less refunds processed on this date.</p><div class="report-feature-footer"><span><span class="online-dot"></span> Source: Shopify</span><strong>{report.bill_count} {report.bill_count===1?'bill':'bills'}</strong></div></section><section class="white-panel report-summary"><span class="kicker">SALES SUMMARY</span><h3>The day at a glance</h3><div class="detail-list"><div><span>Gross sales</span><strong>{money(report.gross_paise)}</strong></div><div><span>Discounts</span><strong>−{money(report.discount_paise)}</strong></div><div><span>Taxable value</span><strong>{money(report.taxable_paise)}</strong></div><div><span>CGST + SGST</span><strong>{money(report.tax_paise)}</strong></div><div><span>Refunds</span><strong>−{money(report.refunds_paise)}</strong></div></div></section><section class="white-panel report-payments"><span class="kicker">PAYMENT BREAKDOWN</span><h3>How customers paid</h3>{#each Object.entries(report.payments) as [type,amount]}<div class="payment-row"><span class="pay-icon"><CreditCard size={20}/></span><div><strong>{type.toUpperCase()}</strong><small>Sales {money(amount.sales_paise)} · Refunds {money(amount.refunds_paise)}</small></div><b>{money(amount.net_paise)}</b></div>{:else}<div class="empty">No payments recorded for this day.</div>{/each}</section><section class="white-panel report-check"><span class="kicker">RECONCILIATION</span><h3>{report.reconciliation_warnings.length?'Needs a closer look':'All accounted for'}</h3>{#if report.reconciliation_warnings.length}{#each report.reconciliation_warnings as warning}<div class="inline-warning"><WarningCircle size={18}/>{warning.reason || `${warning.invoice_or_credit_note}: ${warning.state}`}</div>{/each}{:else}<p>There are no pending or missing records for this store.</p><span class="checkmark"><CheckCircle size={20} weight="fill"/> No discrepancies found</span>{/if}</section></div>{:else}<div class="white-panel empty">{reportLoading?'Loading your report…':'No report available. Check your connection.'}</div>{/if}</div>{/if}
  </main>
  {#if screen !== 'sale'}
    <button class="new-sale-float" class:home-sale-float={screen === 'home'} disabled={booting || busy} on:click={start}>
      <Plus size={20} weight="bold" aria-hidden="true" />
      <span>New sale</span>
    </button>
  {/if}
</div>
{#if showSettings}<dialog class="dialog" use:modal on:close={() => showSettings=false} aria-labelledby="settings-title"><div class="dialog-header"><div><span class="kicker">YOUR WORKSPACE</span><h2 id="settings-title">Store & associate</h2></div><button class="icon-button" aria-label="Close" on:click={() => showSettings=false}><X size={20}/></button></div><p>Choose where you’re working today.</p><label>Store<select bind:value={store} on:change={loadStore}>{#each stores as item}<option value={item.code}>{item.code}</option>{/each}</select></label><label>Associate<select bind:value={staffId} on:change={setStaff}>{#each staff as person}<option value={person.id}>{person.id} · {person.role.replaceAll('_',' ')}</option>{/each}</select></label><button class="primary full" on:click={() => {showSettings=false;loadReport();}}>Done <CheckCircle size={18}/></button></dialog>{/if}
{#if refundConfirm && selectedOrder}<dialog class="dialog" use:modal on:close={() => refundConfirm=false} aria-labelledby="refund-title"><div class="dialog-header"><div><span class="kicker">CONFIRM RETURN</span><h2 id="refund-title">Refund this order?</h2></div><button class="icon-button" aria-label="Close" on:click={() => refundConfirm=false}><X size={20}/></button></div><p>This returns <strong>{money(selectedOrder.total_paise)}</strong> to the original payment method. Frames return to the billing store; lenses are not restocked.</p><div class="dialog-actions"><button class="secondary" on:click={() => refundConfirm=false}>Go back</button><button class="primary" disabled={busy} on:click={refund}>{busy?'Processing…':'Confirm full refund'} <ArrowRight size={17}/></button></div></dialog>{/if}

{#if resetConfirm}<dialog class="dialog" use:modal on:close={() => resetConfirm=false} aria-labelledby="reset-title"><div class="dialog-header"><div><h2 id="reset-title">Start a new sale?</h2></div><button class="icon-button" aria-label="Close" on:click={() => resetConfirm=false}><X size={20}/></button></div><p>The current customer and selections will be cleared.</p><div class="dialog-actions"><button class="secondary" on:click={() => resetConfirm=false}>Keep current sale</button><button class="primary" on:click={beginSale}>Start new sale <ArrowRight size={18}/></button></div></dialog>{/if}

{#if showReceipt && receiptOperation && receiptStore}<ReceiptDialog operation={receiptOperation} store={receiptStore} qr={receiptQr} downloading={invoiceBusy} {error} onClose={() => showReceipt=false} onDownload={saveInvoice}/>{/if}
