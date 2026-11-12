(() => {
  const menuToggle = document.querySelector('.menu-toggle');
  const menu = document.querySelector('.main-nav');
  if (menuToggle && menu) {
    menuToggle.addEventListener('click', () => {
      const isOpen = menu.classList.toggle('is-open');
      menuToggle.setAttribute('aria-expanded', String(isOpen));
    });
  }
  document.querySelectorAll('[data-auto-submit]').forEach(select => {
    select.addEventListener('change', () => select.form?.requestSubmit());
  });
  const mainImage = document.getElementById('gallery-main');
  document.querySelectorAll('[data-gallery-src]').forEach(button => {
    button.addEventListener('click', () => {
      if (!mainImage) return;
      mainImage.src = button.dataset.gallerySrc;
      mainImage.alt = button.dataset.galleryAlt || '';
      document.querySelectorAll('.thumb').forEach(item => item.classList.remove('active'));
      button.classList.add('active');
    });
  });
  const galleryPicker = document.getElementById('gallery_images');
  const count = document.getElementById('gallery-file-count');
  if (galleryPicker && count) {
    galleryPicker.addEventListener('change', () => {
      const n = galleryPicker.files.length;
      count.textContent = n ? `Выбрано фотографий: ${n}` : 'Можно выбрать сразу несколько файлов.';
    });
  }
})();

// Checkout preview only. Server always recalculates authoritative totals.
(() => {
  const select = document.getElementById('id_delivery_method');
  const catalog = document.getElementById('delivery-catalog');
  if (!select || !catalog) return;
  const subtotalEl = document.getElementById('checkout-subtotal');
  const shippingEl = document.getElementById('checkout-shipping');
  const totalEl = document.getElementById('checkout-grand-total');
  const asNumber = raw => Number(String(raw || '0').replace(/\s/g, '').replace(',', '.')) || 0;
  const money = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2, minimumFractionDigits: 0}).format(value) + ' ₽';
  function refresh() {
    const options = [...catalog.querySelectorAll('[data-method-id]')];
    const selected = options.find(option => option.dataset.methodId === select.value);
    options.forEach(option => option.classList.toggle('is-selected', option === selected));
    if (!selected) return;
    const price = asNumber(selected.dataset.methodPrice);
    const subtotal = asNumber(subtotalEl?.dataset.amount);
    if (shippingEl) shippingEl.textContent = money(price);
    if (totalEl) totalEl.textContent = money(subtotal + price);
    const needsAddress = selected.dataset.requiresAddress === '1';
    document.querySelectorAll('[data-address-field]').forEach(wrapper => {
      const input = wrapper.querySelector('input');
      if (!input) return;
      const required = needsAddress && wrapper.dataset.addressField !== 'postal_code';
      input.required = required;
      wrapper.classList.toggle('address-not-required', !needsAddress);
    });
  }
  select.addEventListener('change', refresh);
  refresh();
})();

// NoName: no size is preselected; unavailable sizes are truly disabled.
(() => {
  document.querySelectorAll('[data-size-picker]').forEach(form => {
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector('[data-size-status]');
    const update = () => {
      const chosen = form.querySelector('input[name="size"]:checked:not(:disabled)');
      if (button) {
        button.disabled = !chosen;
        if (button.classList.contains('quick-add-btn')) {
          button.firstChild.textContent = chosen ? `ДОБАВИТЬ ${chosen.value} ` : 'ВЫБЕРИ РАЗМЕР ';
        } else {
          button.firstChild.textContent = chosen ? `ДОБАВИТЬ / ${chosen.value} ` : 'ВЫБЕРИ РАЗМЕР ';
        }
      }
      if (status) status.textContent = chosen ? `Выбран размер ${chosen.value}` : 'Размер пока не выбран';
    };
    form.addEventListener('change', update);
    form.addEventListener('submit', event => {
      if (!form.querySelector('input[name="size"]:checked:not(:disabled)')) event.preventDefault();
    });
    update();
  });
})();

// Shipping calculator on cart. Server is authoritative when the choice is submitted.
(() => {
  const form = document.querySelector('[data-delivery-form]');
  if (!form) return;
  const shippingEl = form.querySelector('[data-shipping-preview]');
  const totalEl = form.querySelector('[data-total-preview]');
  const subtotal = Number(String(form.dataset.subtotal || '0').replace(/\s/g, '').replace(',', '.')) || 0;
  const money = n => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(n) + ' ₽';
  const refresh = () => {
    const chosen = form.querySelector('input[name="delivery_method"]:checked');
    if (!chosen) {
      shippingEl.textContent = 'НЕ ВЫБРАНА';
      totalEl.textContent = money(subtotal) + ' *';
      return;
    }
    const shipping = Number(String(chosen.dataset.methodPrice || '0').replace(/\s/g, '').replace(',', '.')) || 0;
    shippingEl.textContent = money(shipping);
    totalEl.textContent = money(subtotal + shipping);
  };
  form.addEventListener('change', refresh);
  refresh();
})();
