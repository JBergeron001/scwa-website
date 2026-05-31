require('dotenv').config();
const express = require('express');
const cors = require('cors');
const stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);
const { createClient } = require('@supabase/supabase-js');
const { Resend } = require('resend');

const app = express();
const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
const resend = new Resend(process.env.RESEND_API_KEY);

// ── Middleware ──────────────────────────────────────────────
app.use(cors({ origin: process.env.FRONTEND_URL }));
app.use(express.json());

// ── Health check ────────────────────────────────────────────
app.get('/', (req, res) => res.json({ status: 'MAWA backend running' }));

// ── PRICES ──────────────────────────────────────────────────
const PRICES = {
  cert1: 24900,   // $249.00 in cents
  cert2: 24900,
  cert3: 24900,
  package: 89900, // $899.00
  cert4: 29900,   // $299.00
  retry: 5000     // $50.00
};

const CERT_TITLES = {
  1: 'Green Infrastructure Planning and Design',
  2: 'Green Infrastructure Installation and Construction',
  3: 'Green Infrastructure Maintenance and Management',
  4: 'Master of Green Infrastructure'
};

const CERT_BLURBS = {
  1: 'This certifies that the recipient has demonstrated foundational knowledge and competency in green infrastructure principles and applications.',
  2: 'This certifies that the recipient has demonstrated mastery of stormwater management principles and best practices.',
  3: 'This certifies that the recipient has demonstrated advanced proficiency in green infrastructure design and implementation.',
  4: 'This certifies that the recipient has achieved the highest level of mastery in green infrastructure, completing all required certifications through MidAmerica Watersheds Alliance.'
};

// ── CREATE STRIPE PAYMENT INTENT ────────────────────────────
app.post('/api/create-payment-intent', async (req, res) => {
  try {
    const { type, certNumber, userId } = req.body;

    let amount;
    let description;

    if (type === 'package') {
      amount = PRICES.package;
      description = 'MAWA Certification Package (Certs 1, 2 & 3)';
    } else if (type === 'individual') {
      amount = PRICES[`cert${certNumber}`];
      description = `MAWA ${CERT_TITLES[certNumber]} Certification`;
    } else if (type === 'cert4') {
      amount = PRICES.cert4;
      description = `MAWA ${CERT_TITLES[4]} Certification`;
    } else if (type === 'retry') {
      amount = PRICES.retry;
      description = `MAWA Cert ${certNumber} Retry Attempt`;
    } else {
      return res.status(400).json({ error: 'Invalid purchase type' });
    }

    const paymentIntent = await stripe.paymentIntents.create({
      amount,
      currency: 'usd',
      description,
      metadata: { userId, type, certNumber: certNumber?.toString() }
    });

    res.json({ clientSecret: paymentIntent.client_secret, amount });
  } catch (err) {
    console.error('Stripe error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ── CONFIRM PURCHASE (after payment success) ─────────────────
app.post('/api/confirm-purchase', async (req, res) => {
  try {
    const { userId, type, certNumber, paymentMethod, transactionId, amount } = req.body;

    // Get user profile
    const { data: profile } = await supabase
      .from('profiles')
      .select('*')
      .eq('id', userId)
      .single();

    if (!profile) return res.status(404).json({ error: 'User not found' });

    // Log payment
    await supabase.from('payment_log').insert({
      user_id: userId,
      user_number: profile.user_number,
      full_name: profile.full_name,
      email: profile.email,
      amount: amount / 100,
      payment_type: type,
      cert_number: certNumber || null,
      payment_method: paymentMethod,
      transaction_id: transactionId,
      status: 'completed'
    });

    // Create purchase record and enrollments
    if (type === 'package') {
      await supabase.from('purchases').insert({
        user_id: userId, purchase_type: 'package',
        amount_paid: 899.00, payment_method: paymentMethod, transaction_id: transactionId
      });
      // Enroll in certs 1, 2, 3
      for (const num of [1, 2, 3]) {
        await supabase.from('cert_enrollments').upsert({
          user_id: userId, cert_number: num, purchase_type: 'package',
          cert_status: 'in_progress', retry_available: true, retry_used: false
        });
      }
    } else if (type === 'individual') {
      await supabase.from('purchases').insert({
        user_id: userId, purchase_type: 'individual', cert_number: certNumber,
        amount_paid: 249.00, payment_method: paymentMethod, transaction_id: transactionId
      });
      await supabase.from('cert_enrollments').upsert({
        user_id: userId, cert_number: certNumber, purchase_type: 'individual',
        cert_status: 'in_progress', retry_available: false
      });
    } else if (type === 'cert4') {
      await supabase.from('purchases').insert({
        user_id: userId, purchase_type: 'cert4', cert_number: 4,
        amount_paid: 299.00, payment_method: paymentMethod, transaction_id: transactionId
      });
      await supabase.from('cert_enrollments').upsert({
        user_id: userId, cert_number: 4, purchase_type: 'cert4',
        cert_status: 'in_progress', retry_available: false
      });
    } else if (type === 'retry') {
      await supabase.from('purchases').insert({
        user_id: userId, purchase_type: 'retry', cert_number: certNumber,
        amount_paid: 50.00, payment_method: paymentMethod, transaction_id: transactionId
      });
      // Reset exam for one attempt
      await supabase.from('cert_enrollments').update({
        exam_locked: false, exam_attempts_used: 0,
        retry_used: true, retry_available: false,
        cert_status: 'in_progress'
      }).eq('user_id', userId).eq('cert_number', certNumber);
      // Reset all module progress for this cert
      await supabase.from('module_progress').update({
        status: 'not_started', current_slide: 0,
        quiz_passed: false, quiz_attempts: 0
      }).eq('user_id', userId).eq('cert_number', certNumber);
    }

    // Send receipt email
    await sendReceiptEmail(profile, type, certNumber, amount / 100);

    res.json({ success: true });
  } catch (err) {
    console.error('Confirm purchase error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ── SUBMIT FINAL EXAM ────────────────────────────────────────
app.post('/api/submit-exam', async (req, res) => {
  try {
    const { userId, certNumber, answers, questions } = req.body;

    // Get enrollment
    const { data: enrollment } = await supabase
      .from('cert_enrollments')
      .select('*')
      .eq('user_id', userId)
      .eq('cert_number', certNumber)
      .single();

    if (!enrollment) return res.status(404).json({ error: 'Enrollment not found' });
    if (enrollment.exam_locked) return res.status(403).json({ error: 'Exam locked' });

    // Score the exam
    let correct = 0;
    questions.forEach((q, i) => {
      if (answers[i] === q.correctAnswer) correct++;
    });
    const score = Math.round((correct / questions.length) * 100);
    const passed = score >= 80;
    const attemptNumber = enrollment.exam_attempts_used + 1;

    // Log the attempt
    await supabase.from('exam_attempts').insert({
      user_id: userId, cert_number: certNumber,
      attempt_number: attemptNumber, score, passed,
      is_retry: enrollment.retry_used
    });

    // Determine lock logic
    const isPackage = enrollment.purchase_type === 'package';
    const maxAttempts = 3;
    const shouldLock = !passed && attemptNumber >= maxAttempts;
    const retryAvailable = shouldLock && isPackage && !enrollment.retry_used;

    // Update enrollment
    await supabase.from('cert_enrollments').update({
      exam_attempts_used: attemptNumber,
      exam_locked: shouldLock && !retryAvailable,
      retry_available: retryAvailable,
      cert_status: passed ? 'passed' : (shouldLock && !retryAvailable ? 'locked' : 'in_progress'),
      cert_passed_date: passed ? new Date().toISOString() : null
    }).eq('user_id', userId).eq('cert_number', certNumber);

    // If passed — issue certificate and check cert4 unlock
    if (passed) {
      const { data: profile } = await supabase
        .from('profiles').select('*').eq('id', userId).single();

      await supabase.from('issued_certificates').insert({
        user_id: userId,
        user_number: profile.user_number,
        full_name: profile.full_name,
        cert_number: certNumber,
        cert_title: CERT_TITLES[certNumber],
        exam_score: score
      });

      // Check if certs 1,2,3 all passed → unlock cert4
      if ([1, 2, 3].includes(certNumber)) {
        const { data: allCerts } = await supabase
          .from('cert_enrollments')
          .select('cert_number, cert_status')
          .eq('user_id', userId)
          .in('cert_number', [1, 2, 3]);

        const allPassed = allCerts?.length === 3 &&
          allCerts.every(c => c.cert_status === 'passed');

        await sendPassEmail(profile, certNumber, score);
        return res.json({ passed, score, cert4Unlocked: allPassed, retryAvailable: false, locked: false, blurb: CERT_BLURBS[certNumber] });
      }

      await sendPassEmail(profile, certNumber, score);
    }

    res.json({
      passed, score,
      attemptsUsed: attemptNumber,
      locked: shouldLock && !retryAvailable,
      retryAvailable,
      cert4Unlocked: false,
      blurb: passed ? CERT_BLURBS[certNumber] : null
    });

  } catch (err) {
    console.error('Exam submit error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ── SAVE SLIDE PROGRESS ──────────────────────────────────────
app.post('/api/save-progress', async (req, res) => {
  try {
    const { userId, certNumber, moduleNumber, slideIndex } = req.body;
    await supabase.from('module_progress').upsert({
      user_id: userId, cert_number: certNumber,
      module_number: moduleNumber, current_slide: slideIndex,
      status: 'in_progress', last_accessed: new Date().toISOString()
    }, { onConflict: 'user_id,cert_number,module_number' });
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── SUBMIT MODULE QUIZ ───────────────────────────────────────
app.post('/api/submit-quiz', async (req, res) => {
  try {
    const { userId, certNumber, moduleNumber, answers, questions } = req.body;

    let correct = 0;
    questions.forEach((q, i) => {
      if (answers[i] === q.correctAnswer) correct++;
    });
    const score = Math.round((correct / questions.length) * 100);
    const passed = score >= 80;

    const { data: existing } = await supabase
      .from('module_progress')
      .select('quiz_attempts')
      .eq('user_id', userId)
      .eq('cert_number', certNumber)
      .eq('module_number', moduleNumber)
      .single();

    const attempts = (existing?.quiz_attempts || 0) + 1;

    await supabase.from('module_progress').upsert({
      user_id: userId, cert_number: certNumber, module_number: moduleNumber,
      quiz_passed: passed, quiz_score: score, quiz_attempts: attempts,
      status: passed ? 'passed' : 'in_progress',
      quiz_passed_date: passed ? new Date().toISOString() : null,
      // Reset slide position if failed so they redo the module
      current_slide: passed ? undefined : 0
    }, { onConflict: 'user_id,cert_number,module_number' });

    res.json({ passed, score });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── ADMIN: GET ALL USERS ─────────────────────────────────────
app.get('/api/admin/users', async (req, res) => {
  try {
    const { adminKey } = req.query;
    if (adminKey !== process.env.ADMIN_SECRET_KEY) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    const { data } = await supabase
      .from('profiles')
      .select(`*, cert_enrollments(*), issued_certificates(*), payment_log(*)`)
      .order('created_at', { ascending: false });
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── ADMIN: RESET EXAM LOCK ───────────────────────────────────
app.post('/api/admin/reset-exam', async (req, res) => {
  try {
    const { adminKey, userId, certNumber } = req.body;
    if (adminKey !== process.env.ADMIN_SECRET_KEY) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    await supabase.from('cert_enrollments').update({
      exam_locked: false, exam_attempts_used: 0,
      cert_status: 'in_progress'
    }).eq('user_id', userId).eq('cert_number', certNumber);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── EMAIL HELPERS ────────────────────────────────────────────
async function sendReceiptEmail(profile, type, certNumber, amount) {
  const subject = type === 'package'
    ? 'Welcome to MAWA — Certification Package Confirmed'
    : `MAWA — Payment Confirmed`;

  const html = `
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#085041;">MidAmerica Watersheds Alliance</h2>
      <p>Dear ${profile.full_name},</p>
      <p>Thank you for your purchase. Your payment of <strong>$${amount.toFixed(2)}</strong> has been received.</p>
      ${type === 'package' ? '<p>You now have access to <strong>Certifications 1, 2, and 3</strong>. Log in to begin your coursework.</p>' : ''}
      ${type === 'individual' ? `<p>You now have access to <strong>${CERT_TITLES[certNumber]}</strong>. Log in to begin your coursework.</p>` : ''}
      ${type === 'cert4' ? `<p>You now have access to the <strong>Master of Green Infrastructure</strong> certification. Log in to begin.</p>` : ''}
      ${type === 'retry' ? `<p>Your retry attempt for <strong>Certification ${certNumber}</strong> has been activated. You have one attempt remaining.</p>` : ''}
      <p>Your user number is: <strong>#${profile.user_number}</strong></p>
      <p style="margin-top:2rem;color:#666;">MidAmerica Watersheds Alliance<br>Professional Certification Division</p>
    </div>`;

  await resend.emails.send({
    from: 'MAWA Certifications <certs@midamericawatersheds.org>',
    to: profile.email,
    subject,
    html
  });
}

async function sendPassEmail(profile, certNumber, score) {
  await resend.emails.send({
    from: 'MAWA Certifications <certs@midamericawatersheds.org>',
    to: profile.email,
    subject: `Congratulations — You passed ${CERT_TITLES[certNumber]}!`,
    html: `
      <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
        <h2 style="color:#085041;">MidAmerica Watersheds Alliance</h2>
        <p>Dear ${profile.full_name},</p>
        <p>Congratulations! You have successfully passed the <strong>${CERT_TITLES[certNumber]}</strong> certification with a score of <strong>${score}%</strong>.</p>
        <p>${CERT_BLURBS[certNumber]}</p>
        <p>Log in to your account to download your certificate.</p>
        <p style="margin-top:2rem;color:#666;">MidAmerica Watersheds Alliance<br>Professional Certification Division</p>
      </div>`
  });
}

// ── START SERVER ─────────────────────────────────────────────
const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`MAWA backend running on port ${PORT}`));
