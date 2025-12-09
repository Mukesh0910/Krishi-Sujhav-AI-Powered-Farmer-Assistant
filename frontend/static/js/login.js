// Login form functionality and validation for Farmer Assistant

// Login form validation and submission handling
function initializeLoginForm() {
  // Simple client-side validation (server-side validation is primary)
  document.getElementById('loginForm').addEventListener('submit', function(e) {
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;
    
    if (!email || !password) {
      e.preventDefault();
      alert('Please enter both email and password');
    }
  });
}

// Forgot Password functionality
function initializeForgotPassword() {
  const forgotPasswordLink = document.getElementById('forgotPasswordLink');
  const forgotPasswordModal = document.getElementById('forgotPasswordModal');
  const closeForgotModal = document.getElementById('closeForgotModal');
  const backToLoginBtn = document.getElementById('backToLoginBtn');
  const forgotPasswordForm = document.getElementById('forgotPasswordForm');
  const messageDiv = document.getElementById('forgotPasswordMessage');

  // Open modal
  forgotPasswordLink.addEventListener('click', function(e) {
    e.preventDefault();
    forgotPasswordModal.classList.remove('hidden');
    forgotPasswordForm.reset();
    hideMessage();
  });

  // Close modal
  function closeModal() {
    forgotPasswordModal.classList.add('hidden');
    forgotPasswordForm.reset();
    hideMessage();
  }

  closeForgotModal.addEventListener('click', closeModal);
  backToLoginBtn.addEventListener('click', closeModal);

  // Close modal when clicking outside
  forgotPasswordModal.addEventListener('click', function(e) {
    if (e.target === forgotPasswordModal) {
      closeModal();
    }
  });

  // Show message helper
  function showMessage(message, isSuccess) {
    messageDiv.textContent = message;
    messageDiv.className = `p-3 rounded-lg text-sm ${
      isSuccess 
        ? 'bg-green-100 border border-green-400 text-green-700' 
        : 'bg-red-100 border border-red-400 text-red-700'
    }`;
    messageDiv.classList.remove('hidden');
  }

  // Hide message helper
  function hideMessage() {
    messageDiv.classList.add('hidden');
  }

  // Handle form submission
  forgotPasswordForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const email = document.getElementById('resetEmail').value.trim();
    const newPassword = document.getElementById('resetNewPassword').value;
    const confirmPassword = document.getElementById('resetConfirmPassword').value;
    const submitBtn = document.getElementById('resetPasswordBtn');

    // Validation
    if (!email || !newPassword || !confirmPassword) {
      showMessage('Please fill in all fields', false);
      return;
    }

    if (newPassword.length < 6) {
      showMessage('Password must be at least 6 characters long', false);
      return;
    }

    if (newPassword !== confirmPassword) {
      showMessage('Passwords do not match', false);
      return;
    }

    // Disable button and show loading state
    submitBtn.disabled = true;
    submitBtn.innerHTML = `
      <svg class="animate-spin h-5 w-5 mr-2 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      Resetting...
    `;

    try {
      // Call the forgot password API
      const response = await fetch('/forgot-password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email: email,
          new_password: newPassword
        })
      });

      const data = await response.json();

      if (response.ok && data.success) {
        // Success
        showMessage('Password reset successful! You can now login with your new password.', true);
        
        // Clear form
        forgotPasswordForm.reset();
        
        // Close modal after 2 seconds
        setTimeout(() => {
          closeModal();
          // Optionally pre-fill email in login form
          document.getElementById('email').value = email;
        }, 2000);
      } else {
        // Error from server
        showMessage(data.error || 'Failed to reset password. Please try again.', false);
      }
    } catch (error) {
      console.error('Forgot password error:', error);
      showMessage('Network error. Please check your connection and try again.', false);
    } finally {
      // Re-enable button
      submitBtn.disabled = false;
      submitBtn.innerHTML = `
        <svg class="h-5 w-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"></path>
        </svg>
        Reset Password
      `;
    }
  });
}

// Initialize login page functionality
function initializeLoginPage() {
  initializeLoginForm();
  initializeForgotPassword();
}