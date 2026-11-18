// static/js/utils.js

export function getErrorMessage(data) {
    if (!data) return 'Неизвестная ошибка';
    if (typeof data === 'string') return data;
    if (data.error) {
        if (typeof data.error === 'string') return data.error;
        if (data.error.message) {
            if (data.error.details) {
                let detailsStr = '';
                for (let field in data.error.details) {
                    detailsStr += `${field}: ${data.error.details[field].join(', ')}; `;
                }
                return detailsStr ? `${data.error.message}: ${detailsStr}` : data.error.message;
            }
            return data.error.message;
        }
        if (data.error.details) {
            let detailsStr = '';
            for (let field in data.error.details) {
                detailsStr += `${field}: ${data.error.details[field].join(', ')}; `;
            }
            return detailsStr || 'Ошибка валидации';
        }
    }
    return 'Неизвестная ошибка';
}

export function showNotification(message, type = 'error', position = 'top-right') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.setAttribute('role', type === 'error' ? 'alert' : 'status');
    notification.textContent = message;

    let bgColor;
    switch (type) {
        case 'success': bgColor = '#4caf50'; break;
        case 'system': bgColor = '#ffaa00'; break;
        case 'error':
        default: bgColor = '#f44336';
    }

    notification.style.cssText = `
        background: ${bgColor};
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        animation: slideIn 0.3s ease;
        word-wrap: break-word;
    `;

    const normalizedPosition = ['top-right', 'bottom-left', 'top-left', 'bottom-right'].includes(position)
        ? position
        : 'top-right';
    const stackId = `notification-stack-${normalizedPosition}`;
    let stack = document.getElementById(stackId);
    if (!stack) {
        stack = document.createElement('div');
        stack.id = stackId;
        stack.className = `notification-stack ${normalizedPosition}`;
        document.body.appendChild(stack);
    }
    stack.appendChild(notification);
    setTimeout(() => {
        notification.classList.add('is-leaving');
        setTimeout(() => {
            notification.remove();
            if (!stack.children.length) stack.remove();
        }, 200);
    }, 4800);
}
