// Signup form functionality and validation for Farmer Assistant
// Terms and Conditions modal management

// Form validation and submission handling
function initializeSignupForm() {
  // Client-side validation (server-side validation is primary)
  document.getElementById('signupForm').addEventListener('submit', function(e) {
    const fullName = document.getElementById('fullName').value;
    const email = document.getElementById('email').value;
    const location = document.getElementById('location').value;
    const password = document.getElementById('password').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    const languagePreference = document.getElementById('languagePreference').value;
    const termsAccepted = document.getElementById('terms').checked;
    
    // Validate required fields
    if (!fullName || !email || !password || !languagePreference || !termsAccepted) {
      e.preventDefault();
      alert('Please fill in all required fields and accept the terms of service');
      return;
    }
    
    // Validate password match
    if (password !== confirmPassword) {
      e.preventDefault();
      alert('Passwords do not match');
      return;
    }
  });

  // Password confirmation validation
  document.getElementById('confirmPassword').addEventListener('input', function() {
    const password = document.getElementById('password').value;
    const confirmPassword = this.value;
    
    if (password !== confirmPassword) {
      this.setCustomValidity('Passwords do not match');
    } else {
      this.setCustomValidity('');
    }
  });
}

// Terms and Conditions modal functionality
function initializeTermsModal() {
  // Open Terms Modal
  document.getElementById('openTermsModal').addEventListener('click', function(e) {
    e.preventDefault();
    document.getElementById('termsModal').classList.remove('hidden');
  });

  // Open Privacy Modal (same modal for now)
  document.getElementById('openPrivacyModal').addEventListener('click', function(e) {
    e.preventDefault();
    document.getElementById('termsModal').classList.remove('hidden');
  });

  // Close Terms Modal
  document.getElementById('closeTermsModal').addEventListener('click', function() {
    document.getElementById('termsModal').classList.add('hidden');
  });

  // Decline Terms
  document.getElementById('declineTerms').addEventListener('click', function() {
    document.getElementById('terms').checked = false;
    document.getElementById('termsModal').classList.add('hidden');
  });

  // Accept Terms
  document.getElementById('acceptTerms').addEventListener('click', function() {
    document.getElementById('terms').checked = true;
    document.getElementById('termsModal').classList.add('hidden');
  });

  // Close modal when clicking outside
  document.getElementById('termsModal').addEventListener('click', function(e) {
    if (e.target === this) {
      this.classList.add('hidden');
    }
  });
}

// Language change function for real-time translation
function changeLanguage(language) {
  if (language) {
    // Store selected language in localStorage for immediate UI update
    localStorage.setItem('selectedLanguage', language);
    
    // Apply translations immediately (you can expand this with your translation API)
    applyTranslations(language);
  }
}

// Apply translations to the page
function applyTranslations(language) {
  // Translation object - you can expand this or use your translation API
  const translations = {
    hindi: {
      title: 'किसान सहायक में शामिल हों',
      subtitle: 'स्मार्ट खेती शुरू करने के लिए अपना खाता बनाएं',
      languageLabel: 'भाषा प्राथमिकता',
      languagePlaceholder: 'अपनी पसंदीदा भाषा चुनें',
      fullNameLabel: 'पूरा नाम',
      fullNamePlaceholder: 'अपना पूरा नाम दर्ज करें',
      emailLabel: 'ईमेल पता',
      emailPlaceholder: 'farmer@gmail.com',
      passwordLabel: 'पासवर्ड',
      passwordPlaceholder: 'एक मजबूत पासवर्ड बनाएं',
      confirmPasswordLabel: 'पासवर्ड दोबारा टाइप करें',
      confirmPasswordPlaceholder: 'अपने पासवर्ड की पुष्टि करें',
      createButton: 'मेरा खाता बनाएं',
      signInText: 'पहले से खाता है?',
      signInLink: 'यहां साइन इन करें'
    },
    marathi: {
      title: 'शेतकरी सहाय्यकात सामील व्हा',
      subtitle: 'स्मार्ट शेती सुरू करण्यासाठी तुमचे खाते तयार करा',
      languageLabel: 'भाषा प्राधान्य',
      languagePlaceholder: 'तुमची पसंतीची भाषा निवडा',
      fullNameLabel: 'पूर्ण नाव',
      fullNamePlaceholder: 'तुमचे पूर्ण नाव टाका',
      emailLabel: 'ईमेल पत्ता',
      emailPlaceholder: 'farmer@gmail.com',
      passwordLabel: 'पासवर्ड',
      passwordPlaceholder: 'एक मजबूत पासवर्ड तयार करा',
      confirmPasswordLabel: 'पासवर्ड पुन्हा टाइप करा',
      confirmPasswordPlaceholder: 'तुमच्या पासवर्डची पुष्टी करा',
      createButton: 'माझे खाते तयार करा',
      signInText: 'आधीच खाते आहे?',
      signInLink: 'येथे साइन इन करा'
    },
    punjabi: {
      title: 'ਕਿਸਾਨ ਸਹਾਇਕ ਵਿੱਚ ਸ਼ਾਮਲ ਹੋਵੋ',
      subtitle: 'ਸਮਾਰਟ ਖੇਤੀ ਸ਼ੁਰੂ ਕਰਨ ਲਈ ਆਪਣਾ ਖਾਤਾ ਬਣਾਓ',
      languageLabel: 'ਭਾਸ਼ਾ ਤਰਜੀਹ',
      languagePlaceholder: 'ਆਪਣੀ ਪਸੰਦੀਦਾ ਭਾਸ਼ਾ ਚੁਣੋ',
      fullNameLabel: 'ਪੂਰਾ ਨਾਮ',
      fullNamePlaceholder: 'ਆਪਣਾ ਪੂਰਾ ਨਾਮ ਦਰਜ ਕਰੋ',
      emailLabel: 'ਈਮੇਲ ਪਤਾ',
      emailPlaceholder: 'farmer@gmail.com',
      passwordLabel: 'ਪਾਸਵਰਡ',
      passwordPlaceholder: 'ਇੱਕ ਮਜ਼ਬੂਤ ਪਾਸਵਰਡ ਬਣਾਓ',
      confirmPasswordLabel: 'ਪਾਸਵਰਡ ਦੁਬਾਰਾ ਟਾਈਪ ਕਰੋ',
      confirmPasswordPlaceholder: 'ਆਪਣੇ ਪਾਸਵਰਡ ਦੀ ਪੁਸ਼ਟੀ ਕਰੋ',
      createButton: 'ਮੇਰਾ ਖਾਤਾ ਬਣਾਓ',
      signInText: 'ਪਹਿਲਾਂ ਤੋਂ ਖਾਤਾ ਹੈ?',
      signInLink: 'ਇੱਥੇ ਸਾਈਨ ਇਨ ਕਰੋ'
    },
    malayalam: {
      title: 'കർഷക സഹായകനിൽ ചേരുക',
      subtitle: 'സ്മാർട്ട് കൃഷി ആരംഭിക്കാൻ നിങ്ങളുടെ അക്കൗണ്ട് സൃഷ്ടിക്കുക',
      languageLabel: 'ഭാഷാ മുൻഗണന',
      languagePlaceholder: 'നിങ്ങളുടെ ഇഷ്ടപ്പെട്ട ഭാഷ തിരഞ്ഞെടുക്കുക',
      fullNameLabel: 'പൂർണ്ണ നാമം',
      fullNamePlaceholder: 'നിങ്ങളുടെ പൂർണ്ണ നാമം നൽകുക',
      emailLabel: 'ഇമെയിൽ വിലാസം',
      emailPlaceholder: 'farmer@gmail.com',
      passwordLabel: 'പാസ്‌വേഡ്',
      passwordPlaceholder: 'ശക്തമായ പാസ്‌വേഡ് സൃഷ്ടിക്കുക',
      confirmPasswordLabel: 'പാസ്‌വേഡ് വീണ്ടും ടൈപ്പ് ചെയ്യുക',
      confirmPasswordPlaceholder: 'നിങ്ങളുടെ പാസ്‌വേഡ് സ്ഥിരീകരിക്കുക',
      createButton: 'എന്റെ അക്കൗണ്ട് സൃഷ്ടിക്കുക',
      signInText: 'ഇതിനകം അക്കൗണ്ട് ഉണ്ടോ?',
      signInLink: 'ഇവിടെ സൈൻ ഇൻ ചെയ്യുക'
    }
  };
  
  // Apply translations if available
  if (translations[language]) {
    const t = translations[language];
    
    // Update text content
    const titleElement = document.querySelector('h2');
    if (titleElement) titleElement.textContent = t.title;
    
    const subtitleElement = titleElement ? titleElement.nextElementSibling : null;
    if (subtitleElement) subtitleElement.textContent = t.subtitle;
    
    const languageLabel = document.querySelector('label[for="languagePreference"]');
    if (languageLabel) languageLabel.textContent = t.languageLabel;
    
    const fullNameLabel = document.querySelector('label[for="fullName"]');
    if (fullNameLabel) fullNameLabel.textContent = t.fullNameLabel;
    
    const emailLabel = document.querySelector('label[for="email"]');
    if (emailLabel) emailLabel.textContent = t.emailLabel;
    
    const passwordLabel = document.querySelector('label[for="password"]');
    if (passwordLabel) passwordLabel.textContent = t.passwordLabel;
    
    const confirmPasswordLabel = document.querySelector('label[for="confirmPassword"]');
    if (confirmPasswordLabel) confirmPasswordLabel.textContent = t.confirmPasswordLabel;
    
    const createButton = document.querySelector('button[type="submit"] span:last-child');
    if (createButton) createButton.textContent = t.createButton;
    
    // Update placeholders
    const languageSelect = document.getElementById('languagePreference');
    if (languageSelect && languageSelect.options[0]) {
      languageSelect.options[0].textContent = t.languagePlaceholder;
    }
    
    const fullNameInput = document.getElementById('fullName');
    if (fullNameInput) fullNameInput.placeholder = t.fullNamePlaceholder;
    
    const emailInput = document.getElementById('email');
    if (emailInput) emailInput.placeholder = t.emailPlaceholder;
    
    const passwordInput = document.getElementById('password');
    if (passwordInput) passwordInput.placeholder = t.passwordPlaceholder;
    
    const confirmPasswordInput = document.getElementById('confirmPassword');
    if (confirmPasswordInput) confirmPasswordInput.placeholder = t.confirmPasswordPlaceholder;
    
    // Update sign in link
    const signInText = document.querySelector('.text-slate-600');
    if (signInText) {
      signInText.innerHTML = t.signInText + ' <a href="/login" class="font-medium text-accent hover:text-accent/80">' + t.signInLink + '</a>';
    }
  }
}

// Initialize all signup functionality
function initializeSignupPage() {
  initializeSignupForm();
  initializeTermsModal();
}