import os
import logging
import razorpay

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("razor_integration")

class RazorpayGateway:
    def __init__(self):
        # Retrieve keys from environment variables or use test mode placeholders if not set
        self.key_id = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_placeholder_key")
        self.key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "placeholder_secret")
        self.webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "webhook_secret_placeholder")

        # Determine if test mode is active (based on prefix of key id)
        self.is_test_mode = self.key_id.startswith("rzp_test")
        logger.info(f"Razorpay initialized. Key ID: {self.key_id[:10]}... | Mode: {'Test' if self.is_test_mode else 'Live'}")

        try:
            self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
        except Exception as e:
            logger.error(f"Failed to initialize Razorpay Client: {e}")
            self.client = None

    def create_order(self, amount_in_inr, user_id, membership_tier, receipt_id=None):
        """
        Creates a Razorpay Order.
        amount_in_inr: float/int amount (e.g. 9 or 49)
        user_id: ID of the user purchasing
        membership_tier: tier name (e.g. 'Platinum', 'Diamond')
        returns: order dict from Razorpay API
        """
        if not self.client:
            logger.error("Razorpay client is not initialized.")
            raise ValueError("Payment system is currently unavailable.")

        # Convert to paise (Razorpay expects amount in subunits)
        amount_in_paise = int(amount_in_inr * 100)
        
        if not receipt_id:
            import time
            receipt_id = f"rcpt_{user_id}_{int(time.time())}"

        data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": receipt_id,
            "notes": {
                "user_id": str(user_id),
                "membership_tier": membership_tier
            }
        }

        try:
            logger.info(f"Creating Razorpay Order for User {user_id}, Tier: {membership_tier}, Amount: INR {amount_in_inr}")
            order = self.client.order.create(data=data)
            logger.info(f"Razorpay Order created successfully: {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Error creating Razorpay Order: {e}")
            raise

    def verify_payment_signature(self, order_id, payment_id, signature):
        """
        Verifies the Razorpay payment signature returned by the frontend.
        Returns True if signature is valid, False otherwise.
        """
        if not self.client:
            logger.error("Razorpay client is not initialized.")
            return False

        params = {
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        }

        try:
            # client.utility.verify_payment_signature raises an exception if signature is invalid
            self.client.utility.verify_payment_signature(params)
            logger.info(f"Razorpay payment signature verified successfully for Order: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Razorpay payment signature verification failed for Order: {order_id}. Error: {e}")
            return False

    def verify_webhook_signature(self, body, signature):
        """
        Verifies webhook signature using webhook secret.
        body: raw request body bytes/str
        signature: X-Razorpay-Signature header value
        """
        if not self.client:
            logger.error("Razorpay client is not initialized.")
            return False

        try:
            self.client.utility.verify_webhook_signature(body, signature, self.webhook_secret)
            logger.info("Razorpay webhook signature verified successfully.")
            return True
        except Exception as e:
            logger.error(f"Razorpay webhook signature verification failed. Error: {e}")
            return False

    def refund_payment(self, payment_id, amount_in_inr=None):
        """
        Refunds a payment.
        payment_id: Razorpay payment ID to refund
        amount_in_inr: (Optional) Float amount to refund. If None, full refund is issued.
        Returns: refund dict from Razorpay API
        """
        if not self.client:
            logger.error("Razorpay client is not initialized.")
            raise ValueError("Payment system is currently unavailable.")

        data = {}
        if amount_in_inr is not None:
            # Convert to paise
            data["amount"] = int(amount_in_inr * 100)

        try:
            logger.info(f"Initiating Razorpay Refund for Payment {payment_id}, Amount: {amount_in_inr if amount_in_inr else 'Full'}")
            refund = self.client.refund.create(payment_id, data=data)
            logger.info(f"Razorpay Refund processed successfully. Refund ID: {refund.get('id')}")
            return refund
        except Exception as e:
            logger.error(f"Error refunding Razorpay payment {payment_id}: {e}")
            raise

# Global singleton instance
gateway = RazorpayGateway()
