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
  const gallery = document.querySelector('[data-product-gallery]');
  const mainImage = document.getElementById('gallery-main');
  const thumbs = gallery ? [...gallery.querySelectorAll('[data-gallery-src]')] : [];
  const lightbox = document.querySelector('[data-gallery-lightbox]');
  const lightboxImg = lightbox?.querySelector('[data-gallery-lightbox-img]');
  let index = Math.max(0, thumbs.findIndex(thumb => thumb.classList.contains('active')));
  const show = next => {
    if (!thumbs.length || !mainImage) return;
    index = (next + thumbs.length) % thumbs.length;
    const thumb = thumbs[index];
    const src = thumb.dataset.gallerySrc;
    const alt = thumb.dataset.galleryAlt || '';
    mainImage.src = src;
    mainImage.alt = alt;
    if (lightboxImg) {
      lightboxImg.src = src;
      lightboxImg.alt = alt;
    }
    thumbs.forEach(item => item.classList.toggle('active', item === thumb));
  };
  thumbs.forEach((button, thumbIndex) => {
    button.addEventListener('click', () => show(thumbIndex));
  });
  document.querySelectorAll('[data-gallery-step]').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      show(index + Number(button.dataset.galleryStep || '0'));
    });
  });
  const closeLightbox = () => {
    if (!lightbox) return;
    lightbox.hidden = true;
    document.body.classList.remove('lightbox-open');
  };
  gallery?.querySelector('[data-gallery-open]')?.addEventListener('click', () => {
    if (!lightbox) return;
    show(index);
    lightbox.hidden = false;
    document.body.classList.add('lightbox-open');
    lightbox.querySelector('[data-gallery-close]')?.focus();
  });
  lightbox?.querySelector('[data-gallery-close]')?.addEventListener('click', closeLightbox);
  lightbox?.addEventListener('click', event => {
    if (event.target === lightbox) closeLightbox();
  });
  document.addEventListener('keydown', event => {
    if (!lightbox || lightbox.hidden) return;
    if (event.key === 'Escape') {
      closeLightbox();
      return;
    }
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      show(index + (event.key === 'ArrowRight' ? 1 : -1));
    }
  });
  const galleryPicker = document.getElementById('product_photos') || document.getElementById('gallery_images');
  const count = document.getElementById('gallery-file-count');
  if (galleryPicker && count) {
    const empty = count.textContent;
    galleryPicker.addEventListener('change', () => {
      const n = galleryPicker.files.length;
      if (!n) {
        count.textContent = empty;
        return;
      }
      count.textContent = galleryPicker.dataset.coverSet === '1'
        ? `Выбрано фотографий: ${n}. Они добавятся к вещи.`
        : `Выбрано фотографий: ${n}. Первая станет карточкой.`;
    });
  }
})();

// NoName: no size is preselected; stock and preorder modes come from the server-rendered variants.
(() => {
  document.querySelectorAll('[data-size-picker]').forEach(form => {
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector('[data-size-status]');
    const orderTypes = [...form.querySelectorAll('input[name="order_type"]')];
    const chosenMode = () => {
      const checked = orderTypes.find(input => input.checked && !input.disabled);
      if (checked) return checked.value;
      const hidden = orderTypes.find(input => input.type === 'hidden');
      return hidden ? hidden.value : 'stock';
    };
    const update = () => {
      const explicit = orderTypes.some(input => input.type === 'radio');
      const preorderMode = chosenMode() === 'preorder';
      form.querySelectorAll('input[name="size"][data-preorder-size]').forEach(input => {
        const stock = Number(input.dataset.stock || '0');
        input.disabled = explicit && !preorderMode && stock <= 0;
        if (input.disabled) input.checked = false;
      });
      const chosen = form.querySelector('input[name="size"]:checked:not(:disabled)');
      const preorder = explicit ? preorderMode : chosen?.dataset.preorder === '1';
      const hidden = orderTypes.find(input => input.type === 'hidden');
      if (hidden) hidden.value = preorder ? 'preorder' : 'stock';
      const qty = form.querySelector('input[name="quantity"]');
      if (qty) qty.max = '10';
      if (button) {
        button.disabled = !chosen;
        const action = preorder ? 'ПОД ЗАКАЗ' : 'ДОБАВИТЬ';
        button.firstChild.textContent = chosen ? `${action} / ${chosen.value} ` : 'ВЫБЕРИ РАЗМЕР ';
      }
      if (status) {
        if (!chosen) status.textContent = 'Размер пока не выбран';
        else if (preorder) status.textContent = `Размер ${chosen.value}: предзаказ`;
        else status.textContent = `Размер ${chosen.value}: в наличии ${chosen.dataset.stock} шт.`;
      }
    };
    const plate = document.querySelector('[data-preorder-plate]');
    const qtyInput = form.querySelector('input[name="quantity"]');
    const syncPlate = () => {
      if (!plate || form.dataset.allowPreorder !== '1' || plate.dataset.serverOffer === '1') return;
      const chosen = form.querySelector('input[name="size"]:checked:not(:disabled)');
      const qty = Number(qtyInput?.value || '0');
      const stock = Number(chosen?.dataset.stock || '0');
      const over = chosenMode() !== 'preorder' && chosen && qty > stock;
      if (over) openPreorderPlate(plate, {productId: form.dataset.productId, size: chosen.value, quantity: qty, title: form.dataset.productTitle});
      else plate.hidden = true;
    };
    form.addEventListener('change', () => {
      plate?.removeAttribute('data-server-offer');
      update();
      syncPlate();
    });
    qtyInput?.addEventListener('input', () => {
      plate?.removeAttribute('data-server-offer');
      syncPlate();
    });
    form.addEventListener('submit', event => {
      if (!form.querySelector('input[name="size"]:checked:not(:disabled)')) {
        event.preventDefault();
        return;
      }
      plate?.removeAttribute('data-server-offer');
      const chosen = form.querySelector('input[name="size"]:checked:not(:disabled)');
      const qty = Number(qtyInput?.value || '0');
      const stock = Number(chosen?.dataset.stock || '0');
      if (form.dataset.allowPreorder === '1' && chosenMode() !== 'preorder' && chosen && qty > stock) {
        event.preventDefault();
        openPreorderPlate(plate, {productId: form.dataset.productId, size: chosen.value, quantity: qty, title: form.dataset.productTitle});
      }
    });
    update();
  });
})();

function openPreorderPlate(plate, {productId, size, quantity, title}) {
  if (!plate) return;
  const qty = plate.querySelector('[data-preorder-qty]');
  const label = plate.querySelector('[data-preorder-label]');
  if (qty) qty.textContent = String(quantity);
  if (label) label.textContent = title ? ` «${title}», размер ${size}` : '';
  const set = (name, value) => {
    const field = plate.querySelector(`input[name="${name}"]`);
    if (field) field.value = value;
  };
  set('product_id', productId);
  set('size', size);
  set('quantity', String(quantity));
  plate.hidden = false;
}

(() => {
  const plate = document.querySelector('[data-preorder-plate]');
  plate?.querySelector('[data-dismiss-preorder]')?.addEventListener('click', () => {
    plate.hidden = true;
    plate.removeAttribute('data-server-offer');
  });
  document.querySelectorAll('[data-stock-guard]').forEach(form => {
    form.addEventListener('submit', event => {
      if (form.dataset.allowPreorder !== '1') return;
      const submitter = event.submitter;
      if (submitter && submitter.classList.contains('muted-action')) return;
      const qty = Number(form.querySelector('input[name="quantity"]')?.value || '0');
      const stock = Number(form.dataset.stock || '0');
      if (qty > stock) {
        event.preventDefault();
        openPreorderPlate(plate, {
          productId: form.dataset.productId,
          size: form.dataset.size,
          quantity: qty,
          title: form.dataset.productTitle,
        });
      }
    });
  });
})();

(() => {
  document.querySelectorAll('.size-range-item').forEach(item => {
    const box = item.querySelector('input[type="checkbox"]');
    const stock = item.querySelector('input[type="number"]');
    if (!box || !stock) return;
    stock.addEventListener('input', () => {
      if (stock.value !== '') box.checked = true;
    });
    box.addEventListener('change', () => {
      if (box.checked && stock.value === '') stock.value = '0';
    });
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
    if (chosen.dataset.methodPrice === 'quote') {
      shippingEl.textContent = 'ПО ИНДЕКСУ';
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
