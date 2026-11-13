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

// NoName: no size is preselected; stock and preorder modes come from the server-rendered variants.
(() => {
  document.querySelectorAll('[data-size-picker]').forEach(form => {
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector('[data-size-status]');
    const orderType = form.querySelector('input[name="order_type"]');
    const update = () => {
      const chosen = form.querySelector('input[name="size"]:checked:not(:disabled)');
      const preorder = chosen?.dataset.preorder === '1';
      if (orderType) orderType.value = preorder ? 'preorder' : 'stock';
      if (button) {
        button.disabled = !chosen;
        const action = preorder ? 'ПОД ЗАКАЗ' : 'ДОБАВИТЬ';
        button.firstChild.textContent = chosen ? `${action} / ${chosen.value} ` : 'ВЫБЕРИ РАЗМЕР ';
      }
      if (status) {
        status.textContent = chosen
          ? (preorder ? `Размер ${chosen.value}: изготовление под заказ` : `Выбран размер ${chosen.value}`)
          : 'Размер пока не выбран';
      }
    };
    form.addEventListener('change', update);
    form.addEventListener('submit', event => {
      if (!form.querySelector('input[name="size"]:checked:not(:disabled)')) event.preventDefault();
    });
    update();
  });
})();

(() => {
  const addButton = document.querySelector('[data-add-variant]');
  const container = document.querySelector('[data-variant-forms]');
  const template = document.querySelector('[data-variant-template]');
  const totalForms = document.querySelector('#id_variants-TOTAL_FORMS');
  if (!addButton || !container || !template || !totalForms) return;
  addButton.addEventListener('click', () => {
    const index = Number(totalForms.value);
    container.insertAdjacentHTML('beforeend', template.innerHTML.replaceAll('__prefix__', index));
    totalForms.value = index + 1;
  });
  container.addEventListener('click', event => {
    const removeButton = event.target.closest('[data-remove-variant]');
    if (removeButton) removeButton.closest('[data-variant-row]').remove();
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
