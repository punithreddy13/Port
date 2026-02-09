import React from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, Github, Linkedin, Mail } from 'lucide-react';

const Hero = () => {
    return (
        <section className="relative h-screen flex items-center justify-center overflow-hidden bg-neutral-950 text-white">
            {/* Background Gradients */}
            <div className="absolute top-0 left-0 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl -translate-x-1/2 -translate-y-1/2" />
            <div className="absolute bottom-0 right-0 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl translate-x-1/2 translate-y-1/2" />

            <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8 }}
                >
                    <h2 className="text-lg md:text-xl font-medium text-blue-400 mb-4 tracking-wide">
                        HELLO THERE, I'M
                    </h2>
                    <h1 className="text-5xl md:text-7xl font-bold font-['Space_Grotesk'] mb-6 tracking-tight">
                        <span className="bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
                            Punith Reddy
                        </span>
                    </h1>
                    <p className="max-w-2xl mx-auto text-gray-400 text-lg md:text-xl mb-10 leading-relaxed">
                        Building digital experiences with code and creativity.
                        Focusing on modern web technologies and seamless user interfaces.
                    </p>

                    <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                        <motion.a
                            href="#projects"
                            whileHover={{ scale: 1.05 }}
                            whileTap={{ scale: 0.95 }}
                            className="px-8 py-3 bg-white text-neutral-950 rounded-full font-medium flex items-center gap-2 hover:bg-gray-100 transition-colors"
                        >
                            View Work <ArrowRight size={20} />
                        </motion.a>

                        <motion.a
                            href="#contact"
                            whileHover={{ scale: 1.05 }}
                            whileTap={{ scale: 0.95 }}
                            className="px-8 py-3 border border-neutral-700 rounded-full font-medium text-white hover:bg-neutral-800 transition-colors"
                        >
                            Contact Me
                        </motion.a>
                    </div>

                    <div className="mt-12 flex items-center justify-center gap-6 text-gray-400">
                        <a href="#" className="hover:text-white transition-colors transform hover:scale-110">
                            <Github size={24} />
                        </a>
                        <a href="#" className="hover:text-white transition-colors transform hover:scale-110">
                            <Linkedin size={24} />
                        </a>
                        <a href="#" className="hover:text-white transition-colors transform hover:scale-110">
                            <Mail size={24} />
                        </a>
                    </div>

                </motion.div>
            </div>

            {/* Scroll Indicator */}
            <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 1, duration: 1, repeat: Infinity, repeatType: "reverse" }}
                className="absolute bottom-8 left-1/2 transform -translate-x-1/2 text-gray-500 text-sm"
            >
                Scroll Down
            </motion.div>
        </section>
    );
};

export default Hero;
